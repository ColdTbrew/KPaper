import json
import os
import stat
import sys
import tempfile
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import chatgpt_auth as auth
import translate_html_blocks as translator
import paper_qa


def response(body=None, status=200):
    result = Mock()
    result.ok = status < 400
    result.status_code = status
    result.content = b"body" if body is not None else b""
    result.json.return_value = body
    result.headers = {"x-request-id": "request-test"}
    return result


def stream(*events):
    result = response()
    lines = []
    for event in events:
        lines += ["data: " + json.dumps(event), ""]
    result.iter_lines.return_value = iter(lines)
    return result


class ChatGPTAuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = auth.Store(Path(self.temporary.name) / "connection")
        self.addCleanup(self.temporary.cleanup)

    def seed(self, expired=False, scope=True):
        profile = {"client_id": "oaiapp_test", "issuer": auth.ISSUER, "subject": "user-1",
                   "email": "user@example.test", "label": "Connection 1", "access_token": "access-secret",
                   "refresh_token": "refresh-secret", "id_token": "identity-secret",
                   "expires_at": time.time() + (-1 if expired else 3600),
                   "scopes": auth.SCOPES.split() if scope else ["openid"]}
        self.store.write({"host_id": "urn:uuid:host", "active": "oaiapp_test", "profiles": {"oaiapp_test": profile}})
        return profile

    def test_pkce_and_dynamic_registration_then_reuse_issued_client(self):
        first = auth.prepare_authorization("urn:uuid:host", "http://127.0.0.1:1455/auth/callback")
        query = parse_qs(urlparse(first["url"]).query)
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["agent_name_hint"], ["KPaper"])
        self.assertIn("chatgpt.tokens.use.direct", query["scope"][0].split())
        self.assertEqual(query["code_challenge_method"], ["S256"])
        second = auth.prepare_authorization("urn:uuid:host", first["redirect_uri"], self.seed())
        query2 = parse_qs(urlparse(second["url"]).query)
        self.assertEqual(query2["client_id"], ["oaiapp_test"])
        self.assertNotIn("agent_name_hint", query2)
        self.assertNotIn("prompt", query2)
        self.assertNotEqual(first["state"], second["state"])

    def test_loopback_login_exchanges_issued_client_and_keeps_host_on_reauthorization(self):
        identity = {"iss": auth.ISSUER, "sub": "user-1", "email": "user@example.test"}
        tokens = {"id_token": "identity-secret", "access_token": "access-secret",
                  "refresh_token": "refresh-secret", "expires_in": 3600, "scope": auth.SCOPES}
        opened, callback_threads = [], []
        def browser(url):
            parsed = parse_qs(urlparse(url).query)
            opened.append(parsed)
            callback = parsed["redirect_uri"][0] + "?" + urlencode({"state": parsed["state"][0], "code": "code", "client_id": "oaiapp_test"})
            def send_callback():
                with urlopen(callback, timeout=5) as reply:
                    self.assertEqual(reply.status, 200)
            thread = threading.Thread(target=send_callback)
            thread.start()
            callback_threads.append(thread)
            return True
        with patch.object(auth.webbrowser, "open", side_effect=browser), \
                patch.object(auth.requests, "post", return_value=response(tokens)) as post, \
                patch.object(auth, "validate_identity", return_value=identity):
            first = auth.login(store=self.store, timeout=5)
            host = self.store.read()["host_id"]
            second = auth.login("oaiapp_test", self.store, timeout=5)
        for thread in callback_threads:
            thread.join(timeout=5)
        self.assertEqual(first["active"]["id"], "oaiapp_test")
        self.assertEqual(len(second["profiles"]), 1)
        self.assertEqual(host, self.store.read()["host_id"])
        self.assertEqual(opened[0]["client_id"], ["dynamic_agent_client"])
        self.assertEqual(opened[1]["client_id"], ["oaiapp_test"])
        self.assertEqual(post.call_args.kwargs["data"]["client_id"], "oaiapp_test")
        self.assertEqual(post.call_args.kwargs["data"]["redirect_uri"], opened[1]["redirect_uri"][0])

    def test_callback_rejects_replay_missing_registration_and_changed_client(self):
        pending = auth.prepare_authorization("host", "http://127.0.0.1:1234/auth/callback")
        for callback in [dict(state="wrong", code="code", client_id="oaiapp_test"),
                         dict(state=pending["state"], code="code"),
                         dict(state=pending["state"], error="access_denied")]:
            with self.assertRaises(auth.ChatGPTError):
                auth.validate_callback(urlencode(callback), pending)
        valid = auth.validate_callback(urlencode(dict(state=pending["state"], code="code", client_id="oaiapp_test")), pending)
        self.assertEqual(valid["client_id"], "oaiapp_test")
        pending["client_id"] = "oaiapp_test"
        with self.assertRaises(auth.ChatGPTError):
            auth.validate_callback(urlencode(dict(state=pending["state"], code="code", client_id="oaiapp_other")), pending)
        self.assertEqual(auth.validate_callback(urlencode(dict(state=pending["state"], code="code")), pending)["client_id"], "oaiapp_test")

    def test_validates_real_signature_issuer_audience_expiry_and_nonce(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {"iss": auth.ISSUER, "sub": "user", "aud": "oaiapp_test",
                  "iat": int(time.time()), "exp": int(time.time()) + 600, "nonce": "nonce"}
        key_client = Mock()
        key_client.get_signing_key_from_jwt.return_value.key = key.public_key()
        with patch.object(auth, "discovery", return_value={"jwks_uri": auth.ISSUER + "/.well-known/jwks.json"}), \
                patch.object(auth.jwt, "PyJWKClient", return_value=key_client):
            encoded = jwt.encode(claims, key, algorithm="RS256")
            self.assertEqual(auth.validate_identity(encoded, "oaiapp_test", "nonce")["sub"], "user")
            for field, value in [("iss", "https://attacker.test"), ("aud", "other"), ("exp", int(time.time()) - 60), ("nonce", "other")]:
                with self.assertRaises((jwt.InvalidTokenError, auth.ChatGPTError)):
                    auth.validate_identity(jwt.encode(dict(claims, **{field: value}), key, algorithm="RS256"), "oaiapp_test", "nonce")
            wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with self.assertRaises(jwt.InvalidSignatureError):
                auth.validate_identity(jwt.encode(claims, wrong_key, algorithm="RS256"), "oaiapp_test", "nonce")

    def test_credentials_owner_only_and_status_contains_no_tokens(self):
        self.seed()
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)
        result = json.dumps(auth.status(self.store))
        self.assertNotIn("secret", result)
        for field in auth.TOKEN_FIELDS:
            self.assertNotIn('"' + field + '"', result)
        self.assertTrue(auth.status(self.store)["active"]["plan_enabled"])

    def test_identity_only_grant_cannot_run_inference(self):
        self.seed(scope=False)
        with patch.object(auth.requests, "post") as post:
            with self.assertRaisesRegex(auth.ChatGPTError, "plan_permission_required"):
                auth.access_token(self.store)
            post.assert_not_called()

    def test_parallel_batches_rotate_refresh_token_once(self):
        self.seed(expired=True)
        tokens = {"access_token": "renewed", "refresh_token": "rotated", "expires_in": 3600}
        with patch.object(auth.requests, "post", return_value=response(tokens)) as post:
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: auth.access_token(self.store), range(3)))
        self.assertEqual(results, ["renewed"] * 3)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["data"]["client_id"], "oaiapp_test")
        self.assertNotIn("scope", post.call_args.kwargs["data"])
        self.assertEqual(self.store.read()["profiles"]["oaiapp_test"]["refresh_token"], "rotated")

    def test_terminal_refresh_clears_tokens_but_preserves_registration(self):
        self.seed(expired=True)
        with patch.object(auth.requests, "post", return_value=response({"error": "invalid_grant"}, 400)):
            with self.assertRaises(auth.ChatGPTError):
                auth.access_token(self.store)
        profile = self.store.read()["profiles"]["oaiapp_test"]
        self.assertNotIn("refresh_token", profile)
        self.assertEqual(profile["client_id"], "oaiapp_test")

    def test_transient_refresh_failure_preserves_credentials(self):
        self.seed(expired=True)
        with patch.object(auth.requests, "post", return_value=response({"detail": "unavailable"}, 503)):
            with self.assertRaises(auth.ChatGPTError):
                auth.access_token(self.store)
        self.assertEqual(self.store.read()["profiles"]["oaiapp_test"]["refresh_token"], "refresh-secret")

    def test_stream_requires_completed_even_after_receiving_text(self):
        delta = {"type": "response.output_text.delta", "delta": "partial"}
        for terminal in [{"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}},
                         {"type": "response.incomplete"}, None]:
            events = [delta] + ([terminal] if terminal else [])
            with self.assertRaises(auth.ChatGPTError):
                auth.read_stream(stream(*events))
        raw, usage = auth.read_stream(stream(delta, {"type": "response.completed", "response": {"usage": {"input_tokens": 8}}}))
        self.assertEqual(raw, "partial")
        self.assertEqual(usage["input_tokens"], 8)

    def test_models_preserve_account_specific_order_and_slugs(self):
        self.seed()
        body = {"models": [{"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
                           {"slug": "account-model", "display_name": "Available", "visibility": "list"}]}
        with patch.object(auth.requests, "get", return_value=response(body)) as get:
            self.assertEqual(auth.models(self.store), [{"id": "account-model", "display_name": "Available"}])
        self.assertEqual(get.call_args.args[0], "https://api.openai.com/v1/models")

    def test_request_uses_public_streaming_route_and_never_cli_or_api_key(self):
        self.seed()
        result = stream({"type": "response.output_text.delta", "delta": '{"translations":[]}'},
                        {"type": "response.completed", "response": {"usage": {"input_tokens": 7, "output_tokens": 3}}})
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {"KPAPER_CHATGPT_HOME": str(self.store.directory)}), \
                patch.object(auth.requests, "post", return_value=result) as post:
            raw, usage = auth.request_json("account-model", "instructions", [{"role": "user", "content": "text"}], {"type": "object"})
        self.assertEqual(json.loads(raw), {"translations": []})
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/responses")
        payload = post.call_args.kwargs["json"]
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["model"], "account-model")
        self.assertNotIn("temperature", payload)
        self.assertEqual(usage["output_tokens"], 3)

    def test_logout_retains_host_and_registration_and_reports_failed_revocation(self):
        self.seed()
        with patch.object(auth, "discovery", return_value={"revocation_endpoint": auth.ISSUER + "/oauth/revoke"}), \
                patch.object(auth.requests, "post", return_value=response({}, 503)), patch.object(auth.time, "sleep"):
            result = auth.logout(self.store)
        self.assertFalse(result["revocation_confirmed"])
        self.assertFalse(result["active"]["signed_in"])
        self.assertEqual(self.store.read()["host_id"], "urn:uuid:host")
        self.assertEqual(self.store.read()["profiles"]["oaiapp_test"]["client_id"], "oaiapp_test")

    def test_translation_provider_preserves_masked_html_and_token_counts(self):
        raw = json.dumps({"translations": [{"id": "b0", "text": "⟦H0000⟧번역⟦H0001⟧"}]})
        with patch.object(auth, "request_json", return_value=(raw, {"input_tokens": 10, "output_tokens": 5})):
            result = translator.call_chatgpt("gpt-6-luna", [("b0", "⟦H0000⟧English⟦H0001⟧")], 30, 0)
        self.assertEqual(result, ({"b0": "⟦H0000⟧번역⟦H0001⟧"}, 10, 5))

    def test_paper_questions_use_selected_chatgpt_model_and_validated_citations(self):
        path = Path(self.temporary.name) / "paper.html"
        path.write_text('<article><div class="ltx_para" id="p1">The reward is a pass rate.</div></article>')
        result = json.dumps({"answer": "보상은 통과율입니다.", "citations": [{"id": "p1"}]})
        with patch.object(auth, "request_json", return_value=(result, {})) as request:
            answer = paper_qa.ask(path, {"provider": "chatgpt", "model": "account-model", "question": "보상은?"})
        self.assertEqual(answer["citations"][0]["id"], "p1")
        self.assertEqual(request.call_args.args[0], "account-model")
        self.assertIn("The reward is a pass rate.", request.call_args.args[2][0]["content"][0]["text"])

    def test_same_email_registrations_are_kept_separate_when_switching(self):
        first = self.seed()
        data = self.store.read()
        data["profiles"]["oaiapp_other"] = dict(first, client_id="oaiapp_other", label="Connection 2", access_token="other-token")
        self.store.write(data)
        result = auth.select_profile("oaiapp_other", self.store)
        self.assertEqual(len(result["profiles"]), 2)
        self.assertEqual(result["active"]["id"], "oaiapp_other")
        self.assertEqual(auth.access_token(self.store), "other-token")
        self.assertEqual(auth.access_token(self.store, "oaiapp_test"), "access-secret")


if __name__ == "__main__":
    unittest.main()
