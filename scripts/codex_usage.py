"""Read the existing Codex account's weekly limit through official app-server RPC.

This uses Codex's own login, never KPaper's Sign in with ChatGPT credentials.
Only display these limits when account/read matches KPaper's selected email.
"""
from __future__ import annotations

import contextlib
import math
from pathlib import Path
import shutil
import signal
import time


def unavailable(message: str) -> dict:
    return {"available": False, "scope": "codex", "message": message}


def weekly_window(result: dict, email: str, now: float | None = None) -> dict:
    stamp = time.time() if now is None else now
    buckets = result.get("rateLimitsByLimitId")
    # New multi-bucket data takes precedence; never substitute another model's bucket.
    bucket = buckets.get("codex") if isinstance(buckets, dict) else result.get("rateLimits")
    if not isinstance(bucket, dict) or bucket.get("limitId") not in (None, "codex"):
        return unavailable("이 계정의 Codex 주간 한도가 제공되지 않습니다.")
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        if not isinstance(window, dict) or window.get("windowDurationMins") != 7 * 24 * 60:
            continue
        used, resets = window.get("usedPercent"), window.get("resetsAt")
        if (type(used) not in (int, float) or not math.isfinite(used) or
                type(resets) not in (int, float) or not math.isfinite(resets)):
            continue
        if resets <= stamp:
            return unavailable("주간 한도 초기화 이후의 값을 아직 받지 못했습니다. 다시 조회해주세요.")
        used = min(100, max(0, used))
        return {"available": True, "scope": "codex", "account_email": email,
                "used_percent": used, "remaining_percent": 100 - used,
                "resets_at": resets, "updated_at": stamp}
    return unavailable("이 계정의 Codex 주간 한도가 제공되지 않습니다.")


@contextlib.contextmanager
def deadline(seconds: int):
    def expired(*_):
        raise TimeoutError("Codex usage read timed out")
    previous = signal.signal(signal.SIGALRM, expired)
    previous_alarm = signal.alarm(seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        if previous_alarm:
            signal.alarm(max(1, previous_alarm - int(time.monotonic() - started)))


def read_weekly(email: str, timeout: int = 20) -> dict:
    if not email:
        return unavailable("연결 계정의 이메일을 확인할 수 없어 주간 한도를 표시하지 않습니다.")
    try:
        from openai_codex.client import CodexClient, CodexConfig
        from pydantic import BaseModel, ConfigDict

        class RPCResult(BaseModel):
            model_config = ConfigDict(extra="allow")

        local = Path.home() / ".local/bin/codex"
        executable = str(local) if local.is_file() else shutil.which("codex")
        # The official SDK's bundled runtime is the fallback if no installed CLI exists.
        config = CodexConfig(codex_bin=executable, client_name="kpaper", client_title="KPaper",
                             client_version="0.3.3", cwd="/private/tmp")
        with deadline(timeout), CodexClient(config) as client:
            client.initialize()
            def same_account():
                account = client.account_read({"refreshToken": False}).model_dump(by_alias=True).get("account") or {}
                return account.get("type") == "chatgpt" and (account.get("email") or "").casefold() == email.casefold()
            if not same_account():
                return unavailable("Codex와 KPaper의 로그인 계정이 다르거나 Codex에 로그인하지 않았습니다.")
            result = client.request("account/rateLimits/read", {}, response_model=RPCResult).model_dump()
            if not same_account():
                return unavailable("조회 중 Codex 계정이 변경되었습니다. 다시 조회해주세요.")
            return weekly_window(result, email)
    except Exception:
        # SDK errors may carry URLs or diagnostic output; keep them out of UI/logs.
        return unavailable("Codex 주간 한도를 조회하지 못했습니다. 잠시 후 다시 시도해주세요.")
