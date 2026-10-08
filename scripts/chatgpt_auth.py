#!/usr/bin/env python3
"""KPaper's own Sign in with ChatGPT connection and streamed Responses client.

Implements the documented OSS dynamic-client flow, independently of Codex login.
Credential records never leave this process through logs or CLI output.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
import uuid
import webbrowser
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
import requests
from chatgpt_usage import UsageStore

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DEFAULT_MODEL = "gpt-6-luna"
TOKEN_FIELDS = ("access_token", "refresh_token", "id_token", "expires_at")
TERMINAL_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired",
                    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}


class ChatGPTError(RuntimeError):
    def __init__(self, message: str, code: str = "", status: int = 0, request_id: str = ""):
        self.code, self.status, self.request_id = code, status, request_id
        prefix = f"HTTP {status} " if status else ""
        super().__init__(f"{prefix}{code}: {message}" if code else prefix + message)


def storage_dir() -> Path:
    # An override allows isolated tests; credentials always stay outside the repo.
    if os.environ.get("KPAPER_CHATGPT_HOME"):
        return Path(os.environ["KPAPER_CHATGPT_HOME"]).expanduser()
    return Path.home() / "Library/Application Support/KPaper/ChatGPT"


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or storage_dir()
        self.path = self.directory / "accounts.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {"profiles": {}, "active": ""}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: dict) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        fd, filename = tempfile.mkstemp(prefix=".accounts-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)  # mkstemp gives the file mode 0600.
        finally:
            Path(filename).unlink(missing_ok=True)

    @contextlib.contextmanager
    def locked(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        fd = os.open(self.directory / "session.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self.read()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def check_response(response) -> dict:
    if response.ok:
        return response.json() if response.content else {}
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error", {})
    if isinstance(error, dict):
        code, message = error.get("code") or error.get("type", ""), error.get("message", "")
    else:
        code, message = str(error), body.get("error_description", "")
    # Never dump raw HTTP bodies, which may contain credentials or user input.
    message = message or body.get("detail") or "ChatGPT 연결 요청이 실패했습니다."
    raise ChatGPTError(str(message), str(code), response.status_code, response.headers.get("x-request-id", ""))


def discovery() -> dict:
    document = check_response(requests.get(ISSUER + "/.well-known/openid-configuration", timeout=30))
    if document.get("issuer") != ISSUER:
        raise ChatGPTError("OpenAI 인증 서버의 issuer가 일치하지 않습니다.")
    return document


def validate_identity(id_token: str, client_id: str, nonce: str | None = None) -> dict:
    config = discovery()
    jwks = config["jwks_uri"]
    if urlparse(jwks).scheme != "https" or urlparse(jwks).hostname != "auth.openai.com":
        raise ChatGPTError("OpenAI 서명 검증 주소가 올바르지 않습니다.")
    key = jwt.PyJWKClient(jwks, timeout=30).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(id_token, key.key, algorithms=["RS256"], issuer=ISSUER,
                        audience=client_id, leeway=30, options={"require": ["iss", "sub", "aud", "exp", "iat"]})
    if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ChatGPTError("로그인 nonce가 일치하지 않습니다.")
    return claims


def update_tokens(profile: dict, tokens: dict) -> dict:
    updated = dict(profile)
    for field in ("access_token", "refresh_token", "id_token", "token_type"):
        if field in tokens:
            updated[field] = tokens[field]
    if "scope" in tokens:
        updated["scopes"] = tokens["scope"].split()
    updated["expires_at"] = time.time() + int(tokens.get("expires_in", 0))
    return updated


def status(store: Store | None = None) -> dict:
    data = (store or Store()).read()
    profiles = []
    for key, profile in data["profiles"].items():
        profiles.append({"id": key, "label": profile["label"], "email": profile.get("email", ""),
                         "signed_in": bool(profile.get("id_token")),
                         "plan_enabled": bool(profile.get("access_token")) and
                         "chatgpt.tokens.use.direct" in profile.get("scopes", [])})
    active = next((p for p in profiles if p["id"] == data.get("active")), None)
    result = {"profiles": profiles, "active": active, "ok": True}
    # A damaged usage file must not block login or inference readiness.
    try:
        result["usage"] = usage_summary(store, data.get("active", ""))
    except (OSError, ValueError, KeyError, TypeError):
        result["usage_message"] = "사용량 기록을 읽을 수 없습니다."
    return result


def usage_summary(store: Store | None = None, profile_id: str = "") -> dict:
    store = store or Store()
    data = store.read()
    profile_id = profile_id or data.get("active", "")
    if profile_id and profile_id not in data["profiles"]:
        raise ChatGPTError("선택한 ChatGPT 계정을 찾을 수 없습니다.", "profile_not_found")
    return UsageStore(store.directory).summary(profile_id)


def prepare_authorization(host_id: str, redirect_uri: str, profile: dict | None = None) -> dict:
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    profile = profile or {}
    client_id = profile.get("client_id") or "dynamic_agent_client"
    params = {"client_id": client_id, "ext_agent_host_id": host_id, "response_type": "code",
              "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
              "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge}
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = "KPaper"
    elif profile.get("id_token"):
        params["id_token_hint"] = profile["id_token"]
    if profile.get("email"):
        params["login_hint"] = profile["email"]
    if profile and "chatgpt.tokens.use.direct" not in profile.get("scopes", []):
        params["prompt"] = "consent"
    return {"url": AUTHORIZE + "?" + urlencode(params), "state": state, "nonce": nonce,
            "verifier": verifier, "redirect_uri": redirect_uri, "client_id": client_id}


def validate_callback(query: str, pending: dict) -> dict:
    values = parse_qs(query)
    if any(len(v) != 1 for v in values.values()):
        raise ChatGPTError("중복된 로그인 callback 값입니다.")
    values = {k: v[0] for k, v in values.items()}
    if not secrets.compare_digest(values.get("state", ""), pending["state"]):
        raise ChatGPTError("로그인 state가 일치하지 않습니다.", "state_mismatch")
    if values.get("error"):
        raise ChatGPTError("로그인이 취소되었거나 권한이 승인되지 않았습니다.", values["error"])
    client_id = values.get("client_id") or pending["client_id"]
    if pending["client_id"] == "dynamic_agent_client":
        if client_id == "dynamic_agent_client" or not client_id:
            raise ChatGPTError("로그인 서버에서 앱 등록 ID를 받지 못했습니다.")
    elif client_id != pending["client_id"]:
        raise ChatGPTError("선택한 계정의 앱 등록 ID가 일치하지 않습니다.")
    if not values.get("code"):
        raise ChatGPTError("로그인 인증 코드를 받지 못했습니다.")
    return {"code": values["code"], "client_id": client_id}


def login(profile_id: str = "", store: Store | None = None, timeout: int = 300) -> dict:
    store = store or Store()
    with store.locked() as data:
        data.setdefault("host_id", "urn:uuid:" + str(uuid.uuid4()))
        host_id = data["host_id"]
        if profile_id and profile_id not in data["profiles"]:
            raise ChatGPTError("저장된 계정을 찾을 수 없습니다.")
        profile = data["profiles"].get(profile_id)
        if profile is None and data.get("pending_client_id"):
            profile = {"client_id": data["pending_client_id"]}
        store.write(data)
    result = {}
    pending = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # HTTP request URLs contain codes and must not be logged.

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_error(404)
                return
            try:
                callback = validate_callback(parsed.query, pending)
                result.update(callback)
                message = "KPaper로 돌아가 주세요. 로그인 연결을 확인하고 있습니다."
                code = 200
            except ChatGPTError as exc:
                if exc.code != "state_mismatch":
                    result["error"] = exc
                message, code = "로그인 요청을 확인할 수 없습니다. KPaper에서 다시 시도해 주세요.", 400
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(("<!doctype html><meta charset=utf-8><title>KPaper</title><p>" + message).encode())

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
        pending.update(prepare_authorization(host_id, redirect_uri, profile))
        if not webbrowser.open(pending["url"]):
            raise ChatGPTError("로그인 브라우저를 열 수 없습니다. 기본 브라우저 설정을 확인해주세요.")
        deadline = time.monotonic() + timeout
        server.timeout = 0.5
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise ChatGPTError("브라우저 로그인 시간이 초과되었습니다.", "login_timeout")
    if "error" in result:
        raise result["error"]
    if not profile or not profile.get("subject"):
        # Retain an issued registration even if code exchange needs a fresh login.
        with store.locked() as data:
            data["pending_client_id"] = result["client_id"]
            store.write(data)
    tokens = check_response(requests.post(TOKEN, data={"grant_type": "authorization_code",
        "client_id": result["client_id"], "code": result["code"], "code_verifier": pending["verifier"],
        "redirect_uri": redirect_uri, "resource": RESOURCE}, timeout=30))
    identity = validate_identity(tokens["id_token"], result["client_id"], pending["nonce"])
    if profile and profile.get("subject") and (identity["sub"], identity["iss"]) != (profile["subject"], profile["issuer"]):
        raise ChatGPTError("로그인한 계정이 선택한 계정과 다릅니다.")
    with store.locked() as data:
        key = result["client_id"]
        previous = data["profiles"].get(key, {})
        if previous and (previous["subject"], previous["issuer"]) != (identity["sub"], identity["iss"]):
            raise ChatGPTError("기존 앱 등록의 계정과 일치하지 않습니다.")
        record = {"client_id": key, "subject": identity["sub"], "issuer": identity["iss"],
                  "email": identity.get("email", ""), "scopes": [],
                  "label": previous.get("label") or f"{identity.get('email') or 'ChatGPT'} · 연결 {len(data['profiles']) + 1}"}
        data["profiles"][key] = update_tokens(record, tokens)
        data["active"] = key
        if data.get("pending_client_id") == key:
            data.pop("pending_client_id")
        store.write(data)
    return status(store)


def access_token(store: Store | None = None, profile_id: str = "") -> str:
    store = store or Store()
    # The lock serializes rotating refresh tokens across batches and processes.
    with store.locked() as data:
        key = profile_id or data.get("active", "")
        profile = data["profiles"].get(key)
        if not profile or not profile.get("access_token"):
            raise ChatGPTError("설정에서 Continue with ChatGPT로 로그인해주세요.", "login_required")
        if "chatgpt.tokens.use.direct" not in profile.get("scopes", []):
            raise ChatGPTError("이 계정에서 ChatGPT 요금제 사용을 허용해주세요.", "plan_permission_required")
        if profile.get("expires_at", 0) <= time.time() + 60:
            if not profile.get("refresh_token"):
                raise ChatGPTError("로그인 세션이 만료되었습니다. 다시 로그인해주세요.", "login_required")
            try:
                tokens = check_response(requests.post(TOKEN, data={"grant_type": "refresh_token",
                    "client_id": profile["client_id"], "refresh_token": profile["refresh_token"],
                    "resource": RESOURCE}, timeout=30))
            except ChatGPTError as exc:
                if exc.code in TERMINAL_REFRESH:
                    for field in TOKEN_FIELDS:
                        profile.pop(field, None)
                    store.write(data)
                raise
            if tokens.get("id_token"):
                identity = validate_identity(tokens["id_token"], profile["client_id"])
                if (identity["sub"], identity["iss"]) != (profile["subject"], profile["issuer"]):
                    raise ChatGPTError("갱신된 계정 정보가 일치하지 않습니다.")
            profile = update_tokens(profile, tokens)
            data["profiles"][key] = profile
            store.write(data)
            if "chatgpt.tokens.use.direct" not in profile.get("scopes", []):
                raise ChatGPTError("ChatGPT 요금제 사용 권한이 없습니다.", "plan_permission_required")
        return profile["access_token"]


def models(store: Store | None = None) -> list[dict]:
    body = check_response(requests.get(RESOURCE + "/models",
        headers={"Authorization": "Bearer " + access_token(store)}, timeout=30))
    return [{"id": m["slug"], "display_name": m["display_name"]}
            for m in body["models"] if m.get("visibility") == "list"]


def select_profile(profile_id: str, store: Store | None = None) -> dict:
    store = store or Store()
    with store.locked() as data:
        if profile_id not in data["profiles"]:
            raise ChatGPTError("저장된 계정을 찾을 수 없습니다.")
        data["active"] = profile_id
        store.write(data)
    return status(store)


def logout(store: Store | None = None) -> dict:
    store = store or Store()
    revoked = True
    with store.locked() as data:
        profile = data["profiles"].get(data.get("active", ""))
        if profile:
            if profile.get("refresh_token"):
                revoked = False
                for attempt in range(3):
                    try:
                        endpoint = discovery()["revocation_endpoint"]
                        if urlparse(endpoint).scheme != "https" or urlparse(endpoint).hostname != "auth.openai.com":
                            raise ChatGPTError("세션 해제 주소가 올바르지 않습니다.")
                        response = requests.post(endpoint, data={"token": profile["refresh_token"],
                            "token_type_hint": "refresh_token", "client_id": profile["client_id"]}, timeout=15)
                        revoked = response.status_code == 200
                        if revoked or response.status_code < 500:
                            break
                    except (requests.RequestException, ChatGPTError):
                        pass
                    if attempt < 2:
                        time.sleep(2 ** attempt)
            for field in TOKEN_FIELDS:
                profile.pop(field, None)
            store.write(data)
    result = status(store)
    result["revocation_confirmed"] = revoked
    return result


def read_stream(response, on_usage=None) -> tuple[str, dict]:
    response.encoding = "utf-8"
    text, usage = [], {}
    event_lines = []
    # Parse SSE records, including multi-line data, rather than individual lines.
    for line in response.iter_lines(decode_unicode=True):
        if line.startswith("data:"):
            event_lines.append(line[5:].lstrip())
        if line or not event_lines:
            continue
        raw = "\n".join(event_lines)
        event_lines.clear()
        if raw == "[DONE]":
            break
        event = json.loads(raw)
        kind = event.get("type", "")
        if kind == "response.output_text.delta":
            text.append(event["delta"])
        elif kind in ("response.failed", "response.incomplete", "error"):
            if on_usage and kind != "error":
                on_usage(event.get("response", {}), False)
            detail = event.get("response", {}).get("error") or event.get("error") or event
            raise ChatGPTError(detail.get("message") or "ChatGPT 응답을 완료하지 못했습니다.",
                               detail.get("code") or kind, request_id=response.headers.get("x-request-id", ""))
        elif kind == "response.completed":
            if on_usage:
                on_usage(event["response"], True)
            usage = event["response"].get("usage") or {}
            return "".join(text), usage
    raise ChatGPTError("ChatGPT 응답 스트림이 완료 전에 끊겼습니다.", "stream_interrupted")


def request_json(model: str, instructions: str, inputs: list, schema: dict,
                 timeout: int = 180, retries: int = 0) -> tuple[str, dict]:
    # Pin the account once per request, even if another process switches accounts.
    store = Store()
    profile_id = store.read().get("active", "")
    def record_usage(response: dict, completed: bool):
        try:
            UsageStore(store.directory).record(profile_id, response, completed)
        except (OSError, ValueError, KeyError, TypeError):
            # Recording trouble must not discard an otherwise valid translation.
            warnings.warn("KPaper 토큰 사용량 기록을 저장하지 못했습니다.", RuntimeWarning)
    payload = {"model": model, "instructions": instructions, "input": inputs,
               "store": False, "stream": True, "reasoning": {"effort": "low"},
               "text": {"format": {"type": "json_schema", "name": "kpaper_response", "strict": True, "schema": schema}}}
    for attempt in range(retries + 1):
        try:
            with requests.post(RESOURCE + "/responses", headers={"Authorization": "Bearer " + access_token(store, profile_id)},
                               json=payload, stream=True, timeout=timeout) as response:
                if not response.ok:
                    check_response(response)
                return read_stream(response, record_usage)
        except ChatGPTError as exc:
            if exc.status not in (502, 503, 504) or exc.code in {
                "subscription_sharing_user_not_eligible", "subscription_sharing_usage_limit_exceeded",
                "subscription_sharing_unsupported_capability", "subscription_sharing_route_not_supported",
                "chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context"
            } or attempt == retries:
                raise
        except requests.RequestException as exc:
            if attempt == retries:
                # requests exception text can contain a full URL; emit a safe message.
                raise ChatGPTError("ChatGPT 연결이 끊겼거나 응답 시간이 초과되었습니다.", "connection_error") from exc
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "login", "models", "select", "logout", "check", "usage", "weekly"])
    parser.add_argument("--profile", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            result = login(args.profile)
        elif args.command == "models":
            result = {"ok": True, "models": models()}
        elif args.command == "select":
            result = select_profile(args.profile)
        elif args.command == "logout":
            result = logout()
        elif args.command == "check":
            access_token()
            result = status()
        elif args.command == "usage":
            result = {"ok": True, "usage": usage_summary(profile_id=args.profile)}
        elif args.command == "weekly":
            import codex_usage
            data = status()
            profile = next((p for p in data["profiles"] if p["id"] == args.profile), None) if args.profile else data["active"]
            weekly = (codex_usage.read_weekly(profile["email"]) if profile and profile["signed_in"]
                      else codex_usage.unavailable("설정에서 ChatGPT에 로그인해주세요."))
            result = {"ok": True, "weekly_usage": weekly}
        else:
            result = status()
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Do not emit JWT library exceptions, tokens, prompts, or authorization URLs.
        message = str(exc) if isinstance(exc, ChatGPTError) else "ChatGPT 연결을 확인할 수 없습니다. 다시 시도해주세요."
        print(json.dumps({"ok": False, "message": message,
                          "code": getattr(exc, "code", ""), "status": getattr(exc, "status", 0),
                          "request_id": getattr(exc, "request_id", "")}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
