import asyncio
import os
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import mcp.types as mt
import pytest
from fastmcp.tools import ToolResult

from linkedin_mcp_server import browser_memory, process_tree
from linkedin_mcp_server.browser_memory import (
    BrowserMemoryExceeded,
    ensure_memory_headroom,
    memory_limit_bytes,
    run_within_memory_limit,
)
from linkedin_mcp_server.error_handler import CodedToolError, ErrorCode
from linkedin_mcp_server.sequential_tool_middleware import (
    SequentialToolExecutionMiddleware,
)

GB = 1024**3
_SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def memory(monkeypatch):
    monkeypatch.delenv("LINKEDIN_BROWSER_MAX_MEMORY_GB", raising=False)
    monkeypatch.setattr(browser_memory, "_WATCH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(browser_memory, "_warned_unmeasured", False)
    state = SimpleNamespace(used=0)
    monkeypatch.setattr(browser_memory, "browser_memory_bytes", lambda: state.used)
    return state


def _eventually(predicate, within: float = 10.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process scan")
def test_counts_only_processes_carrying_a_browser_marker(monkeypatch):
    name = f"LINKEDIN_MCP_TEST_MARKER_{secrets.token_hex(4)}"
    monkeypatch.setattr(process_tree, "_BROWSER_PROCESS_MARKER", name)
    unmarked = subprocess.Popen(_SLEEPER)
    try:
        assert process_tree.browser_memory_bytes() == 0
        marked = subprocess.Popen(_SLEEPER, env={**os.environ, name: "any-launch"})
        try:
            assert _eventually(lambda: (process_tree.browser_memory_bytes() or 0) > 0)
        finally:
            marked.kill()
            marked.wait()
    finally:
        unmarked.kill()
        unmarked.wait()


def test_default_limit_is_ten_gigabytes(memory):
    assert memory_limit_bytes() == 10 * GB


def test_limit_comes_from_the_environment(memory, monkeypatch):
    monkeypatch.setenv("LINKEDIN_BROWSER_MAX_MEMORY_GB", "2.5")

    assert memory_limit_bytes() == int(2.5 * GB)


async def test_headroom_leaves_the_browser_open_under_the_limit(memory):
    memory.used = 9 * GB
    close = AsyncMock()

    await ensure_memory_headroom(close)

    close.assert_not_awaited()


async def test_headroom_closes_this_browser_when_over_the_limit(memory):
    memory.used = 12 * GB

    async def close() -> None:
        memory.used = 1 * GB

    await ensure_memory_headroom(close)

    assert memory.used == 1 * GB


async def test_headroom_refuses_when_closing_this_browser_is_not_enough(memory):
    memory.used = 12 * GB
    close = AsyncMock()

    with pytest.raises(BrowserMemoryExceeded, match=r"12\.0 GB.*10 GB"):
        await ensure_memory_headroom(close)

    close.assert_awaited_once()


async def test_unknown_usage_does_not_block_and_warns_once(memory, caplog):
    memory.used = None
    close = AsyncMock()

    await ensure_memory_headroom(close)
    await ensure_memory_headroom(close)

    close.assert_not_awaited()
    warnings = [r for r in caplog.records if "not enforced" in r.getMessage()]
    assert len(warnings) == 1


async def test_a_call_under_the_limit_returns_its_result(memory):
    memory.used = 2 * GB
    close = AsyncMock()

    async def call() -> str:
        await asyncio.sleep(0.05)
        return "done"

    assert await run_within_memory_limit(call, close) == ("done", None)
    close.assert_not_awaited()


async def test_a_call_that_returns_despite_a_breach_reports_it(memory):
    closed = asyncio.Event()

    async def close() -> None:
        closed.set()

    async def call() -> str:
        memory.used = 11 * GB
        await closed.wait()
        return "send unconfirmed"

    result, breach = await run_within_memory_limit(call, close)

    assert result == "send unconfirmed"
    assert breach is not None
    assert "11.0 GB" in str(breach)
    assert "may be incomplete" in str(breach)


async def test_a_triggered_close_finishes_after_the_call_ends(memory):
    started = asyncio.Event()
    finished = asyncio.Event()

    async def close() -> None:
        started.set()
        await asyncio.sleep(0.2)
        finished.set()

    async def call() -> str:
        memory.used = 11 * GB
        await started.wait()
        raise RuntimeError("Target page, context or browser has been closed")

    with pytest.raises(BrowserMemoryExceeded):
        await run_within_memory_limit(call, close)

    assert finished.is_set()


async def test_crossing_the_limit_mid_call_closes_the_browser(memory):
    closed = asyncio.Event()

    async def close() -> None:
        closed.set()

    async def call() -> str:
        memory.used = 11 * GB
        await closed.wait()
        raise RuntimeError("Target page, context or browser has been closed")

    with pytest.raises(BrowserMemoryExceeded, match=r"11\.0 GB") as caught:
        await run_within_memory_limit(call, close)

    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_a_failure_without_a_breach_passes_through(memory):
    async def call() -> str:
        raise ValueError("unrelated")

    with pytest.raises(ValueError, match="unrelated"):
        await run_within_memory_limit(call, AsyncMock())


def _context(tool: str = "search_people") -> MagicMock:
    context = MagicMock(fastmcp_context=None)
    context.message.name = tool
    return context


async def test_middleware_refuses_a_call_while_browsers_stay_over_the_limit(
    memory, monkeypatch
):
    memory.used = 11 * GB
    close = AsyncMock()
    monkeypatch.setattr("linkedin_mcp_server.drivers.browser.close_browser", close)
    call_next = AsyncMock()

    with pytest.raises(CodedToolError, match=r"11\.0 GB") as caught:
        await SequentialToolExecutionMiddleware().on_call_tool(_context(), call_next)

    assert caught.value.code is ErrorCode.BROWSER_UNAVAILABLE
    close.assert_awaited_once()
    call_next.assert_not_awaited()


async def test_middleware_fails_a_call_whose_browser_crossed_the_limit(
    memory, monkeypatch
):
    closed = asyncio.Event()

    async def close() -> None:
        closed.set()

    monkeypatch.setattr("linkedin_mcp_server.drivers.browser.close_browser", close)

    async def cut_by_the_limit(_context):
        memory.used = 11 * GB
        await closed.wait()
        raise CodedToolError("Page closed", ErrorCode.PAGE_UNREADABLE)

    call_next = AsyncMock(side_effect=cut_by_the_limit)

    with pytest.raises(CodedToolError, match=r"11\.0 GB") as caught:
        await SequentialToolExecutionMiddleware().on_call_tool(_context(), call_next)

    assert caught.value.code is ErrorCode.BROWSER_UNAVAILABLE


async def test_middleware_keeps_an_unknown_outcome_when_the_limit_cuts_a_write(
    memory, monkeypatch
):
    closed = asyncio.Event()

    async def close() -> None:
        closed.set()

    monkeypatch.setattr("linkedin_mcp_server.drivers.browser.close_browser", close)

    async def cut_by_the_limit(_context):
        memory.used = 11 * GB
        await closed.wait()
        raise CodedToolError("Send may have happened", ErrorCode.OUTCOME_UNKNOWN)

    call_next = AsyncMock(side_effect=cut_by_the_limit)

    with pytest.raises(CodedToolError, match=r"11\.0 GB") as caught:
        await SequentialToolExecutionMiddleware().on_call_tool(
            _context("send_message"), call_next
        )

    assert caught.value.code is ErrorCode.OUTCOME_UNKNOWN


async def test_middleware_releases_the_lease_when_the_headroom_check_fails(
    memory, monkeypatch
):
    memory.used = 11 * GB
    lease = MagicMock()
    lease.try_acquire.return_value = True
    monkeypatch.setattr(
        "linkedin_mcp_server.sequential_tool_middleware.get_profile_lease",
        lambda: lease,
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.close_browser",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await SequentialToolExecutionMiddleware().on_call_tool(_context(), AsyncMock())

    lease.release.assert_called_once()


async def test_middleware_adds_a_notice_to_a_result_returned_despite_a_breach(
    memory, monkeypatch
):
    closed = asyncio.Event()

    async def close() -> None:
        closed.set()

    monkeypatch.setattr("linkedin_mcp_server.drivers.browser.close_browser", close)

    async def unconfirmed_send(_context):
        memory.used = 11 * GB
        await closed.wait()
        return ToolResult(
            content=[mt.TextContent(type="text", text="send unconfirmed")],
            structured_content={"status": "send_unconfirmed", "retry_safe": False},
        )

    result = await SequentialToolExecutionMiddleware().on_call_tool(
        _context("send_message"), AsyncMock(side_effect=unconfirmed_send)
    )

    assert result.structured_content == {
        "status": "send_unconfirmed",
        "retry_safe": False,
    }
    texts = [item.text for item in result.content if isinstance(item, mt.TextContent)]
    assert texts[0] == "send unconfirmed"
    assert "11.0 GB" in texts[-1]
