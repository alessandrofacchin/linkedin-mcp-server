"""Pacing of LinkedIn page loads against an hourly budget shared by every process."""

import asyncio
import json
import os
import random
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

NAV_LOG_PATH = Path.home() / ".linkedin-mcp" / "nav-times.json"

LOCKOUT_PATH = Path.home() / ".linkedin-mcp" / "lockout.json"

_WINDOW_SECONDS = 3600
_DAY_SECONDS = 86400
_EXEMPT_PATHS = ("/login", "/checkpoint", "/uas/", "/authwall")


class NavigationBudgetExceeded(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _now() -> float:
    return time.time()


def _max_per_hour() -> int:
    return int(os.environ.get("LINKEDIN_NAV_MAX_PER_HOUR", "15"))


def _max_per_day() -> int:
    return int(os.environ.get("LINKEDIN_NAV_MAX_PER_DAY", "50"))


def _lockout_seconds() -> float:
    return float(os.environ.get("LINKEDIN_LOCKOUT_HOURS", "24")) * 3600


def _day_times(now: float) -> list[float]:
    try:
        raw = json.loads(NAV_LOG_PATH.read_text())
        return [t for t in raw if now - t < _DAY_SECONDS]
    except (OSError, ValueError, TypeError):
        return []


def _recent_times(now: float) -> list[float]:
    return [t for t in _day_times(now) if now - t < _WINDOW_SECONDS]


def _write_times(times: list[float]) -> None:
    NAV_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=NAV_LOG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(times, f)
        os.replace(tmp, NAV_LOG_PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remaining_loads(now: float | None = None) -> int:
    now = _now() if now is None else now
    return max(0, _max_per_hour() - len(_recent_times(now)))


def loads_needed_fit(n: int) -> bool:
    return remaining_loads() >= n


def _active_lockout(now: float) -> dict | None:
    try:
        lock = json.loads(LOCKOUT_PATH.read_text())
        return lock if float(lock["until"]) > now else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def record_lockout(reason: str) -> None:
    LOCKOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCKOUT_PATH.write_text(
        json.dumps({"until": _now() + _lockout_seconds(), "reason": reason})
    )


def _duration(seconds: int) -> str:
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


async def pace_navigation(url: str) -> None:
    parts = urlsplit(url)
    if not parts.netloc.endswith("linkedin.com"):
        return
    now = _now()
    lock = _active_lockout(now)
    if lock:
        until = datetime.fromtimestamp(float(lock["until"])).strftime("%Y-%m-%d %H:%M")
        raise NavigationBudgetExceeded(
            f"LinkedIn tools locked until {until} after {lock.get('reason')}. "
            "Delete ~/.linkedin-mcp/lockout.json only if Alessandro explicitly "
            "says so.",
            int(float(lock["until"]) - now),
        )
    if parts.path.startswith(_EXEMPT_PATHS):
        return
    min_gap = float(os.environ.get("LINKEDIN_NAV_MIN_GAP", "20"))
    jitter = float(os.environ.get("LINKEDIN_NAV_JITTER", "25"))
    per_hour = _max_per_hour()
    per_day = _max_per_day()
    day = _day_times(now)
    if len(day) >= per_day:
        retry_after = int(_DAY_SECONDS - (now - min(day)))
        raise NavigationBudgetExceeded(
            f"Local LinkedIn daily page-load budget reached ({per_day}/24h); "
            f"next slot in {_duration(retry_after)}. "
            "Stop LinkedIn work for today.",
            retry_after,
        )
    times = [t for t in day if now - t < _WINDOW_SECONDS]
    if len(times) >= per_hour:
        retry_after = int(_WINDOW_SECONDS - (now - min(times)))
        raise NavigationBudgetExceeded(
            f"Local LinkedIn page-load budget reached ({per_hour}/hour); "
            f"next slot in {retry_after}s",
            retry_after,
        )
    if times:
        wait = max(times) + min_gap + random.uniform(0, jitter) - now
        if wait > 0:
            await asyncio.sleep(wait)
    day.append(_now())
    _write_times(day)
