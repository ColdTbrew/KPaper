import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_usage


class CodexUsageTests(unittest.TestCase):
    def window(self, used=27, duration=10080, reset=2000):
        return {"usedPercent": used, "windowDurationMins": duration, "resetsAt": reset}

    def test_weekly_window_can_be_primary_or_secondary(self):
        for name in ("primary", "secondary"):
            result = codex_usage.weekly_window({"rateLimitsByLimitId": {"codex": {name: self.window()}}}, "user@example.test", 1000)
            self.assertTrue(result["available"])
            self.assertEqual((result["used_percent"], result["remaining_percent"]), (27, 73))
            self.assertEqual(result["resets_at"], 2000)
            self.assertEqual(result["scope"], "codex")

    def test_multi_bucket_view_takes_precedence_and_never_substitutes_another_bucket(self):
        old = {"primary": self.window(95)}
        payload = {"rateLimits": old, "rateLimitsByLimitId": {"codex": {"primary": self.window(20)}}}
        self.assertEqual(codex_usage.weekly_window(payload, "a", 1000)["remaining_percent"], 80)
        payload["rateLimitsByLimitId"] = {"other-model": old}
        self.assertFalse(codex_usage.weekly_window(payload, "a", 1000)["available"])
        self.assertEqual(codex_usage.weekly_window({"rateLimits": old}, "a", 1000)["remaining_percent"], 5)

    def test_missing_non_weekly_invalid_or_expired_values_do_not_become_100_percent_remaining(self):
        for window in [None, self.window(duration=300), self.window(used=None), self.window(used=True),
                       self.window(used=float("nan")), self.window(reset=None), self.window(reset=1000)]:
            result = codex_usage.weekly_window({"rateLimits": {"primary": window}}, "a", 1000)
            self.assertFalse(result["available"])
            self.assertNotIn("remaining_percent", result)

    def test_server_percentage_is_clamped(self):
        for used, remaining in [(-1, 100), (101, 0), (27.5, 72.5), (0, 100)]:
            result = codex_usage.weekly_window({"rateLimits": {"primary": self.window(used)}}, "a", 1000)
            self.assertEqual(result["remaining_percent"], remaining)

    def fake_cli(self, home, account="user@example.test", switched=False, hang=False):
        cli = home / ".local/bin/codex"
        cli.parent.mkdir(parents=True)
        events = home / "rpc.jsonl"
        config = {"email": account, "switched": switched, "hang": hang,
                  "events": str(events), "resets": time.time() + 3600}
        cli.write_text("#!" + sys.executable + "\n" + '''
import json, sys, time
config = json.loads(CONFIG)
reads = 0
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    with open(config['events'], 'a') as log:
        log.write(json.dumps(request) + '\\n')
    if 'id' not in request:
        continue
    if method == 'initialize':
        result = {'userAgent': 'codex_cli_rs/0.160.1'}
    elif method == 'account/read':
        reads += 1
        email = 'different@example.test' if config['switched'] and reads > 1 else config['email']
        result = {'account': {'type': 'chatgpt', 'email': email, 'planType': 'pro'}, 'requiresOpenaiAuth': True}
    elif method == 'account/rateLimits/read':
        if config['hang']:
            time.sleep(10)
        result = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 27, 'windowDurationMins': 10080, 'resetsAt': config['resets']}}}}
    else:
        raise AssertionError('Unexpected method: ' + method)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
'''.replace("CONFIG", repr(json.dumps(config))))
        cli.chmod(0o755)
        return events

    def test_official_rpc_read_uses_matching_account_and_never_starts_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            events = self.fake_cli(home)
            with patch.object(codex_usage.Path, "home", return_value=home):
                result = codex_usage.read_weekly("user@example.test")
            self.assertEqual(result["remaining_percent"], 73)
            requests = [json.loads(line) for line in events.read_text().splitlines()]
            self.assertEqual([r["method"] for r in requests], ["initialize", "initialized", "account/read", "account/rateLimits/read", "account/read"])
            for request in requests:
                self.assertNotIn("accessToken", json.dumps(request))

    def test_different_account_is_rejected_before_reading_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            events = self.fake_cli(home, account="different@example.test")
            with patch.object(codex_usage.Path, "home", return_value=home):
                result = codex_usage.read_weekly("user@example.test")
            self.assertFalse(result["available"])
            self.assertNotIn("account/rateLimits/read", events.read_text())

    def test_account_switch_during_query_discards_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.fake_cli(home, switched=True)
            with patch.object(codex_usage.Path, "home", return_value=home):
                result = codex_usage.read_weekly("user@example.test")
            self.assertFalse(result["available"])
            self.assertNotIn("remaining_percent", result)

    def test_unresponsive_rpc_has_a_bounded_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.fake_cli(home, hang=True)
            started = time.monotonic()
            with patch.object(codex_usage.Path, "home", return_value=home):
                result = codex_usage.read_weekly("user@example.test", timeout=1)
            self.assertLess(time.monotonic() - started, 4)
            self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
