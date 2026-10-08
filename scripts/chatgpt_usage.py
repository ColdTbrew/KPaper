"""Local ChatGPT token totals reported by KPaper's Responses requests.

These counters describe this Mac's KPaper traffic, never a subscription quota.
No prompts, answers, credentials, or response headers are stored here.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time


def counters() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "requests": 0, "incomplete_requests": 0}


class UsageStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "usage.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {"profiles": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    @contextlib.contextmanager
    def locked(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        fd = os.open(self.directory / "usage.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self.read()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def write(self, data: dict):
        fd, filename = tempfile.mkstemp(prefix=".usage-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)
        finally:
            Path(filename).unlink(missing_ok=True)

    def record(self, profile_id: str, response: dict, completed: bool, now: float | None = None):
        usage = response.get("usage")
        if not profile_id or not isinstance(usage, dict):
            return
        # A missing usage object means unknown consumption, rather than zero.
        values = [usage.get(key) for key in ("input_tokens", "output_tokens")]
        if any(type(value) is not int or value < 0 for value in values):
            return
        stamp = time.time() if now is None else now
        today = datetime.fromtimestamp(stamp).astimezone().date()
        cutoff = (today - timedelta(days=29)).isoformat()
        response_id = response.get("id")
        with self.locked() as data:
            profile = data["profiles"].setdefault(profile_id, {
                "days": {}, "seen": {}, "tracking_started_at": stamp})
            profile["days"] = {day: value for day, value in profile["days"].items() if day >= cutoff}
            profile["seen"] = {key: day for key, day in profile["seen"].items() if day >= cutoff}
            if response_id and response_id in profile["seen"]:
                return
            day = profile["days"].setdefault(today.isoformat(), counters())
            day["input_tokens"] += values[0]
            day["output_tokens"] += values[1]
            day["total_tokens"] += sum(values)
            day["requests"] += 1
            day["incomplete_requests"] += int(not completed)
            if response_id:
                profile["seen"][response_id] = today.isoformat()
            profile["updated_at"] = stamp
            self.write(data)

    def summary(self, profile_id: str, now: float | None = None) -> dict:
        stamp = time.time() if now is None else now
        today = datetime.fromtimestamp(stamp).astimezone().date()
        profile = self.read()["profiles"].get(profile_id, {})
        days = profile.get("days", {})
        total = counters()
        for offset in range(30):
            for key, value in days.get((today - timedelta(days=offset)).isoformat(), {}).items():
                if key in total:
                    total[key] += value
        daily = [{"date": (today - timedelta(days=offset)).isoformat(),
                  **days.get((today - timedelta(days=offset)).isoformat(), counters())}
                 for offset in reversed(range(7))]
        return {"profile_id": profile_id, "scope": "kpaper_on_this_mac", "quota_available": False,
                "today": days.get(today.isoformat(), counters()), "last_30_days": total, "daily": daily,
                "tracking_started_at": profile.get("tracking_started_at"), "updated_at": profile.get("updated_at")}
