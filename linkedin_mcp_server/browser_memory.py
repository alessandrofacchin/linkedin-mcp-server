import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import TypeVar

from linkedin_mcp_server.process_tree import browser_memory_bytes

logger = logging.getLogger(__name__)

T = TypeVar("T")

_GB = 1024**3
_WATCH_INTERVAL_SECONDS = 2.0
_warned_unmeasured = False


class BrowserMemoryExceeded(RuntimeError):
    def __init__(self, used: int, limit: int, detail: str) -> None:
        super().__init__(
            f"LinkedIn MCP browsers are using {used / _GB:.1f} GB, over the "
            f"{limit / _GB:g} GB limit (LINKEDIN_BROWSER_MAX_MEMORY_GB). {detail}"
        )


def memory_limit_bytes() -> int:
    return int(float(os.environ.get("LINKEDIN_BROWSER_MAX_MEMORY_GB", "10")) * _GB)


async def _usage_over_limit() -> int | None:
    global _warned_unmeasured
    used = await asyncio.to_thread(browser_memory_bytes)
    if used is None:
        if not _warned_unmeasured:
            _warned_unmeasured = True
            logger.warning(
                "Browser memory cannot be measured here, so "
                "LINKEDIN_BROWSER_MAX_MEMORY_GB is not enforced"
            )
        return None
    if used < memory_limit_bytes():
        return None
    return used


async def ensure_memory_headroom(close_browser: Callable[[], Awaitable[None]]) -> None:
    if await _usage_over_limit() is None:
        return
    await close_browser()
    used = await _usage_over_limit()
    if used is not None:
        raise BrowserMemoryExceeded(
            used,
            memory_limit_bytes(),
            "This server's browser is closed, so another LinkedIn MCP process "
            "holds the rest; end it before retrying.",
        )


async def run_within_memory_limit(
    call: Callable[[], Awaitable[T]],
    close_browser: Callable[[], Awaitable[None]],
) -> tuple[T, BrowserMemoryExceeded | None]:
    breach: int | None = None

    async def watch() -> None:
        nonlocal breach
        while breach is None:
            await asyncio.sleep(_WATCH_INTERVAL_SECONDS)
            breach = await _usage_over_limit()
        logger.warning(
            "LinkedIn MCP browsers reached %.1f GB; closing this server's browser",
            breach / _GB,
        )
        try:
            await close_browser()
        except Exception:
            logger.warning(
                "Closing the browser over the memory limit failed", exc_info=True
            )

    watcher = asyncio.create_task(watch(), name="linkedin-browser-memory")
    try:
        result = await call()
    except Exception as exc:
        if breach is None:
            raise
        raise BrowserMemoryExceeded(
            breach,
            memory_limit_bytes(),
            "The browser was closed during this call.",
        ) from exc
    finally:
        if breach is None:
            watcher.cancel()
        await asyncio.wait([watcher])
    if breach is None:
        return result, None
    return result, BrowserMemoryExceeded(
        breach,
        memory_limit_bytes(),
        "The browser was closed during this call, so this result may be incomplete.",
    )
