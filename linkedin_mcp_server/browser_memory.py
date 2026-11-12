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


class BrowserMemoryExceeded(RuntimeError):
    def __init__(self, used: int, limit: int, detail: str) -> None:
        super().__init__(
            f"LinkedIn MCP browsers are using {used / _GB:.1f} GB, over the "
            f"{limit / _GB:g} GB limit (LINKEDIN_BROWSER_MAX_MEMORY_GB). {detail}"
        )


def memory_limit_bytes() -> int:
    return int(float(os.environ.get("LINKEDIN_BROWSER_MAX_MEMORY_GB", "10")) * _GB)


async def _usage_over_limit() -> int | None:
    used = await asyncio.to_thread(browser_memory_bytes)
    if used is None or used < memory_limit_bytes():
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
) -> T:
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
        await close_browser()

    watcher = asyncio.create_task(watch(), name="linkedin-browser-memory")
    try:
        return await call()
    except Exception as exc:
        if breach is None:
            raise
        raise BrowserMemoryExceeded(
            breach,
            memory_limit_bytes(),
            "The browser was closed during this call.",
        ) from exc
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
