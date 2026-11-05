"""Browser-DOM tests for resolving the profile compose button.

These cases execute the target-resolution JavaScript against synthetic
Chromium DOMs. No LinkedIn page is loaded and no account action is performed.
"""

from __future__ import annotations

import os

import pytest
from patchright.async_api import async_playwright

from linkedin_mcp_server.linkedin.message_sender import _PROFILE_MESSAGE_TARGET_JS

pytestmark = [
    pytest.mark.browser_dom,
    pytest.mark.xdist_group("browser_runtime"),
]

COMPOSE = "https://www.linkedin.com/messaging/compose/"


def _anchor(query: str, label: str = "Message") -> str:
    return f'<a href="{COMPOSE}?{query}">{label}</a>'


def _profile(anchors: str, aside: str = "") -> str:
    return f"""<main>
      {aside}
      <section>
        <section>
          <h1>Test User</h1>{anchors}
          <section><h2>Open to work</h2><p>Product designer roles</p></section>
        </section>
      </section>
    </main>"""


async def _dom_page():
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                channel="chromium", headless=True
            )
            page = await browser.new_page()
        except Exception as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            yield page
        finally:
            await browser.close()


@pytest.fixture
async def dom_page():
    async for page in _dom_page():
        yield page


async def _resolve(page, body: str) -> dict:
    await page.set_content(f"<!DOCTYPE html><html><body>{body}</body></html>")
    return await page.evaluate(_PROFILE_MESSAGE_TARGET_JS)


async def test_open_to_work_profile_resolves(dom_page):
    result = await _resolve(dom_page, _profile(_anchor("recipient=ACoAAB1")))

    assert result["status"] == "resolved"
    assert result["displayName"] == "Test User"
    assert result["composeHrefs"] == [f"{COMPOSE}?recipient=ACoAAB1"]


async def test_two_anchors_same_recipient_resolve(dom_page):
    anchors = _anchor("recipient=ACoAAB1") + _anchor(
        "profileUrn=urn:li:fsd_profile:ACoAAB1", "Message again"
    )

    result = await _resolve(dom_page, _profile(anchors))

    assert result["status"] == "resolved"
    assert len(result["composeHrefs"]) == 1
    assert "ACoAAB1" in result["composeHrefs"][0]


async def test_two_anchors_different_recipients_unresolved(dom_page):
    anchors = _anchor("recipient=ACoAAB1") + _anchor("recipient=ACoAAB2")

    result = await _resolve(dom_page, _profile(anchors))

    assert result["status"] == "unresolved"
    assert "ACoAAB1" in result["reason"]
    assert "ACoAAB2" in result["reason"]


async def test_anchor_in_aside_ignored(dom_page):
    aside = (
        f"<aside><section><h2>People you may know</h2>"
        f"{_anchor('recipient=ACoAAB2')}</section></aside>"
    )

    result = await _resolve(dom_page, _profile(_anchor("recipient=ACoAAB1"), aside))

    assert result["status"] == "resolved"
    assert result["composeHrefs"] == [f"{COMPOSE}?recipient=ACoAAB1"]
