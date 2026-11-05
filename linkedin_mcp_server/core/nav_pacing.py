"""Pacing of LinkedIn page loads against an hourly budget shared by every process."""

import asyncio
import json
import os
import random
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

NAV_LOG_PATH = Path.home() / ".linkedin-mcp" / "nav-times.json"

_WINDOW_SECONDS = 3600
_EXEMPT_PATHS = ("/login", "/checkpoint", "/uas/", "/authwall")


class NavigationBudgetExceeded(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _now() -> float:
    return time.time()


def _max_per_hour() -> int:
    return int(os.environ.get("LINKEDIN_NAV_MAX_PER_HOUR", "30"))


def _recent_times(now: float) -> list[float]:
    try:
        raw = json.loads(NAV_LOG_PATH.read_text())
        return [t for t in raw if now - t < _WINDOW_SECONDS]
    except (OSError, ValueError, TypeError):
        return []


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


async def pace_navigation(url: str) -> None:
    parts = urlsplit(url)
    if not parts.netloc.endswith("linkedin.com") or parts.path.startswith(
        _EXEMPT_PATHS
    ):
        return
    min_gap = float(os.environ.get("LINKEDIN_NAV_MIN_GAP", "20"))
    jitter = float(os.environ.get("LINKEDIN_NAV_JITTER", "25"))
    per_hour = _max_per_hour()
    now = _now()
    times = _recent_times(now)
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
    times.append(_now())
    _write_times(times)
