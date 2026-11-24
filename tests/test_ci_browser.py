"""The CI browser is really there: Playwright launches Chromium and renders a page.

Skips locally when Playwright is missing. In CI (UFO_REQUIRE_BROWSER=1) that skip is turned into a failure by
tests/conftest.py, so the rendered accessibility checks cannot silently drop out.
"""
import pytest


def test_chromium_renders_a_page():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 400, "height": 300})
            page.set_content("<button id='b' style='width:44px;height:44px'>x</button>")
            box = page.locator("#b").bounding_box()
            browser.close()
    except Exception as exc:  # no browser binary installed
        pytest.skip(f"browser unavailable: {exc}")
    assert box["width"] == 44 and box["height"] == 44
