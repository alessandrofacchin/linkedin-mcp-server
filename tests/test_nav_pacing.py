import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from linkedin_mcp_server.core import nav_pacing
from linkedin_mcp_server.core.nav_pacing import (
    NavigationBudgetExceeded,
    loads_needed_fit,
    pace_navigation,
    remaining_loads,
)
from linkedin_mcp_server.core.proxy_errors import goto_reporting_proxy_errors

URL = "https://www.linkedin.com/in/someone/"
NOW = 1_000_000.0


@pytest.fixture
def pacing(monkeypatch, tmp_path):
    log = tmp_path / "pacing" / "nav-times.json"
    monkeypatch.setattr(nav_pacing, "NAV_LOG_PATH", log)
    monkeypatch.delenv("LINKEDIN_NAV_MIN_GAP", raising=False)
    monkeypatch.delenv("LINKEDIN_NAV_JITTER", raising=False)
    monkeypatch.delenv("LINKEDIN_NAV_MAX_PER_HOUR", raising=False)
    monkeypatch.setattr(nav_pacing, "_now", lambda: NOW)
    monkeypatch.setattr(nav_pacing.random, "uniform", lambda lo, hi: hi)
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(nav_pacing.asyncio, "sleep", record_sleep)

    def write(times: list[float]) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps(times))

    return SimpleNamespace(log=log, sleeps=sleeps, write=write)


async def test_first_load_does_not_wait(pacing):
    await pace_navigation(URL)

    assert pacing.sleeps == []
    assert json.loads(pacing.log.read_text()) == [NOW]


async def test_second_load_waits_min_gap_plus_jitter(pacing):
    pacing.write([NOW - 5])

    await pace_navigation(URL)

    assert pacing.sleeps == [40]
    assert len(json.loads(pacing.log.read_text())) == 2


async def test_cap_raises_with_retry_after(pacing):
    pacing.write([NOW - 3000 + i for i in range(30)])

    with pytest.raises(NavigationBudgetExceeded) as raised:
        await pace_navigation(URL)

    assert raised.value.retry_after_seconds == 600
    assert "30/hour" in str(raised.value)
    assert pacing.sleeps == []


async def test_old_entries_expire(pacing):
    pacing.write([NOW - 4000] * 30)

    await pace_navigation(URL)

    assert json.loads(pacing.log.read_text()) == [NOW]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/login",
        "https://www.linkedin.com/checkpoint/x",
        "https://www.linkedin.com/uas/login",
        "https://www.linkedin.com/authwall",
        "https://example.com/",
    ],
)
async def test_exempt_paths_and_foreign_hosts_skip(pacing, url):
    pacing.write([NOW - 1])

    await pace_navigation(url)

    assert pacing.sleeps == []
    assert json.loads(pacing.log.read_text()) == [NOW - 1]


async def test_corrupt_log_is_treated_as_empty(pacing):
    pacing.log.parent.mkdir(parents=True)
    pacing.log.write_text("not json")

    await pace_navigation(URL)

    assert json.loads(pacing.log.read_text()) == [NOW]


async def test_env_overrides(pacing, monkeypatch):
    monkeypatch.setenv("LINKEDIN_NAV_MAX_PER_HOUR", "2")
    pacing.write([NOW - 100, NOW - 50])

    with pytest.raises(NavigationBudgetExceeded):
        await pace_navigation(URL)


def test_remaining_loads(pacing):
    pacing.write([NOW - 10 * i for i in range(7)])

    assert remaining_loads() == 23
    assert loads_needed_fit(23)
    assert not loads_needed_fit(24)


async def test_goto_wrapper_paces_before_navigating(monkeypatch):
    order: list[str] = []
    pace = AsyncMock(side_effect=lambda url: order.append(f"pace {url}"))
    monkeypatch.setattr("linkedin_mcp_server.core.proxy_errors.pace_navigation", pace)

    class FakePage:
        def on(self, *args):
            pass

        def remove_listener(self, *args):
            pass

        async def goto(self, url, **kwargs):
            order.append(f"goto {url}")

    await goto_reporting_proxy_errors(FakePage(), URL)

    pace.assert_awaited_once_with(URL)
    assert order == [f"pace {URL}", f"goto {URL}"]
