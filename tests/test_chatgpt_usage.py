import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import chatgpt_auth as auth
from chatgpt_usage import UsageStore
from test_chatgpt_auth import stream


class ChatGPTUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "chatgpt"
        self.usage = UsageStore(self.directory)
        self.now = datetime(2026, 10, 8, 12).timestamp()

    def record(self, response_id, input_tokens=10, output_tokens=5, profile="a", days_ago=0, completed=True):
        self.usage.record(profile, {"id": response_id, "usage": {
            "input_tokens": input_tokens, "output_tokens": output_tokens}}, completed,
            now=self.now - days_ago * 86400)

    def test_empty_usage_is_zero_with_no_invented_quota(self):
        summary = self.usage.summary("a", self.now)
        self.assertEqual(summary["today"]["total_tokens"], 0)
        self.assertEqual(summary["today"]["requests"], 0)
        self.assertEqual(len(summary["daily"]), 7)
        self.assertFalse(summary["quota_available"])
        self.assertEqual(summary["scope"], "kpaper_on_this_mac")
        self.assertIsNone(summary["tracking_started_at"])
        self.assertFalse(self.directory.exists(), "Viewing usage needs neither login nor a writable credential file")

    def test_parallel_requests_are_atomic_and_duplicate_responses_not_counted_twice(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.record("response-" + str(i)), range(80)))
        self.record("response-0")
        totals = self.usage.summary("a", self.now)["today"]
        self.assertEqual(totals["requests"], 80)
        self.assertEqual(totals["input_tokens"], 800)
        self.assertEqual(totals["output_tokens"], 400)
        self.assertEqual(totals["total_tokens"], 1200)
        self.assertEqual(stat.S_IMODE(self.usage.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_calendar_day_rollover_and_exact_30_day_window(self):
        for age in [30, 29, 6, 1, 0]:
            self.record(f"r-{age}", days_ago=age)
        summary = self.usage.summary("a", self.now)
        self.assertEqual(summary["today"]["requests"], 1)
        self.assertEqual(summary["last_30_days"]["requests"], 4)
        self.assertEqual([day["requests"] for day in summary["daily"]], [1, 0, 0, 0, 0, 1, 1])
        tomorrow = self.usage.summary("a", self.now + 86400)
        self.assertEqual(tomorrow["today"]["total_tokens"], 0)
        self.assertEqual(tomorrow["last_30_days"]["requests"], 3)
        stored = self.usage.read()["profiles"]["a"]
        expired = (datetime.fromtimestamp(self.now).date() - timedelta(days=30)).isoformat()
        self.assertNotIn(expired, stored["days"])
        self.assertNotIn("r-30", stored["seen"])

    def test_account_totals_never_mix_even_with_same_response_id(self):
        self.record("shared-id", 30, 10, "a")
        self.record("shared-id", 4, 2, "b")
        self.assertEqual(self.usage.summary("a", self.now)["today"]["total_tokens"], 40)
        self.assertEqual(self.usage.summary("b", self.now)["today"]["total_tokens"], 6)
        self.assertEqual(self.usage.summary("new", self.now)["today"]["total_tokens"], 0)

    def test_unknown_or_invalid_usage_is_not_treated_as_a_known_zero_request(self):
        for usage in [None, {}, {"input_tokens": 5}, {"input_tokens": -1, "output_tokens": 2},
                      {"input_tokens": True, "output_tokens": 2}, {"input_tokens": "5", "output_tokens": 2}]:
            self.usage.record("a", {"id": "invalid", "usage": usage}, True, self.now)
        self.assertEqual(self.usage.summary("a", self.now)["today"]["requests"], 0)
        self.record("known-zero", 0, 0)
        self.assertEqual(self.usage.summary("a", self.now)["today"]["requests"], 1)

    def seed_account(self):
        auth.Store(self.directory).write({"profiles": {"a": {"label": "A", "client_id": "a",
            "id_token": "identity-secret", "access_token": "access-secret", "expires_at": self.now + 3600,
            "scopes": auth.SCOPES.split()}}, "active": "a"})

    def request_with_stream(self, result):
        from unittest.mock import Mock
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {"KPAPER_CHATGPT_HOME": str(self.directory)}), \
                patch.object(auth, "access_token", return_value="access-secret"), \
                patch.object(auth.requests, "post", return_value=result):
            return auth.request_json("gpt-6-luna", "private instructions", [{"role": "user", "content": "private prompt"}], {})

    def test_completed_and_incomplete_streams_record_reported_usage_without_content(self):
        self.seed_account()
        complete = stream({"type": "response.output_text.delta", "delta": "private answer"},
            {"type": "response.completed", "response": {"id": "complete", "usage": {"input_tokens": 9, "output_tokens": 3}}})
        self.assertEqual(self.request_with_stream(complete)[0], "private answer")
        incomplete = stream({"type": "response.incomplete", "response": {"id": "incomplete",
            "usage": {"input_tokens": 4, "output_tokens": 2}}})
        with self.assertRaises(auth.ChatGPTError):
            self.request_with_stream(incomplete)
        totals = self.usage.summary("a")["today"]
        self.assertEqual((totals["total_tokens"], totals["requests"], totals["incomplete_requests"]), (18, 2, 1))
        saved = self.usage.path.read_text()
        for private in ["secret", "private", "instructions", "answer", "prompt"]:
            self.assertNotIn(private, saved)

    def test_failed_stream_and_interruption_do_not_invent_consumption(self):
        self.seed_account()
        for result in [stream({"type": "response.failed", "response": {"error": {"code": "limit"}}}),
                       stream({"type": "response.output_text.delta", "delta": "partial"})]:
            with self.assertRaises(auth.ChatGPTError):
                self.request_with_stream(result)
        self.assertEqual(self.usage.summary("a")["today"]["requests"], 0)

    def test_damaged_ledger_is_preserved_and_does_not_discard_valid_inference(self):
        self.seed_account()
        self.usage.path.write_text("invalid json")
        result = stream({"type": "response.output_text.delta", "delta": "result"},
            {"type": "response.completed", "response": {"id": "r", "usage": {"input_tokens": 9, "output_tokens": 3}}})
        with self.assertWarnsRegex(RuntimeWarning, "기록을 저장하지 못했습니다"):
            self.assertEqual(self.request_with_stream(result)[0], "result")
        self.assertEqual(self.usage.path.read_text(), "invalid json")
        status = auth.status(auth.Store(self.directory))
        self.assertTrue(status["ok"])
        self.assertIn("usage_message", status)

    def test_usage_cli_is_local_and_has_no_credentials(self):
        self.seed_account()
        self.record("r")
        with patch.dict(os.environ, {"KPAPER_CHATGPT_HOME": str(self.directory)}), \
                patch.object(auth.requests, "get") as get, patch.object(auth.requests, "post") as post:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                auth.main(["usage", "--profile", "a"])
            get.assert_not_called()
            post.assert_not_called()
        data = json.loads(output.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(data["usage"]["today"]["total_tokens"], 15)
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn("access_token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
