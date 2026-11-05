from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from patchright.async_api import async_playwright

from linkedin_mcp_server.linkedin.connection_removal import ConnectionRemover
from linkedin_mcp_server.linkedin.connection_support import ConnectionFailure
from linkedin_mcp_server.linkedin.session import PageSession

pytestmark = [
    pytest.mark.browser_dom,
    pytest.mark.xdist_group("browser_runtime"),
]

ROW = (
    "<main><div><a href='/in/ada/'>Ada</a>"
    "<button aria-expanded='false'>more</button></div></main>"
)


SWALLOWING_ROW = (
    "<main><div><a href='/in/ada/'>Ada</a>"
    "<button aria-expanded='false' id='b'>more</button></div>"
    "<script>let n = 0;"
    "document.getElementById('b').addEventListener('click', (e) => {"
    "if (++n < 2) return;"
    "e.target.setAttribute('aria-expanded', 'true');"
    "const m = document.createElement('div');"
    "m.setAttribute('role', 'menu'); m.innerHTML = '<div role=\\'menuitem\\'>Remove connection</div>';"
    "document.body.appendChild(m);});</script></main>"
)


async def test_a_swallowed_first_click_is_retried_once(monkeypatch):
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(channel="chromium", headless=True)
    except Exception:
        pytest.skip("chromium is not installed")
    try:
        page = await browser.new_page()
        await page.set_content(SWALLOWING_ROW)
        remover = ConnectionRemover(PageSession(page), MagicMock())

        async def stay_on_page(url: str) -> None:
            return None

        monkeypatch.setattr(remover, "_goto", stay_on_page)
        monkeypatch.setattr(
            "linkedin_mcp_server.linkedin.connection_removal._SETTLE_MS", 10
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.linkedin.connection_removal._FIRST_CLICK_TIMEOUT_MS",
            200,
        )
        result = await remover.remove("ada", dry_run=True)
        assert result["status"] == "WOULD_REMOVE"
    finally:
        await browser.close()
        await playwright.stop()


async def test_a_menu_that_never_opens_reports_the_page_state(monkeypatch):
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(channel="chromium", headless=True)
    except Exception:
        pytest.skip("chromium is not installed")
    try:
        page = await browser.new_page()
        await page.set_content(ROW)
        remover = ConnectionRemover(PageSession(page), MagicMock())

        async def stay_on_page(url: str) -> None:
            return None

        monkeypatch.setattr(remover, "_goto", stay_on_page)
        monkeypatch.setattr(
            "linkedin_mcp_server.linkedin.connection_removal._MENU_TIMEOUT_MS", 200
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.linkedin.connection_removal._SETTLE_MS", 10
        )
        with pytest.raises(ConnectionFailure) as failure:
            await remover.remove("ada", dry_run=True)
        state = failure.value.details["menuState"]
        assert state["menusAll"] == 0
        assert state["menusVisible"] == 0
        assert state["box"]["width"] > 0
        assert state["hit"]["inOpener"] is True
        assert {"viewport", "outer", "scroll", "expanded", "hasFocus"} <= set(state)
    finally:
        await browser.close()
        await playwright.stop()
