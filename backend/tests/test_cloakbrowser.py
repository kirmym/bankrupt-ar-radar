"""Regression tests for browser transport classification."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.connectors.cloakbrowser import CloakBrowserHttpError, fetch_html_via_cloakbrowser


class _FakePage:
    def __init__(self, response, body: str = "ok"):
        self._response = response
        self._body = body
        self.url = response.url if response is not None else "https://source.example/page"

    async def goto(self, *_args, **_kwargs):
        return self._response

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def locator(self, _selector: str):
        return self

    async def inner_text(self, **_kwargs) -> str:
        return self._body

    async def content(self) -> str:
        return "<html><body>ok</body></html>"

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def connect_over_cdp(self, _cdp_url: str):
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_browser_navigation_http_status_is_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    import playwright.async_api as playwright_api

    response = SimpleNamespace(status=404, url="https://source.example/missing")
    page = _FakePage(response, body="Страница не найдена")
    browser = SimpleNamespace(contexts=[_FakeContext(page)])
    monkeypatch.setattr(
        playwright_api,
        "async_playwright",
        lambda: _FakePlaywright(browser),
    )

    with pytest.raises(CloakBrowserHttpError, match="status=404") as exc_info:
        await fetch_html_via_cloakbrowser(
            "https://source.example/missing",
            cdp_url="http://127.0.0.1:9222",
            allowed_hosts={"source.example"},
            wait_seconds=0,
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.url == "https://source.example/missing"


@pytest.mark.asyncio
async def test_browser_error_page_is_not_returned_as_source_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import playwright.async_api as playwright_api

    page = _FakePage(None, body="404 — Страница не найдена")
    browser = SimpleNamespace(contexts=[_FakeContext(page)])
    monkeypatch.setattr(
        playwright_api,
        "async_playwright",
        lambda: _FakePlaywright(browser),
    )

    with pytest.raises(CloakBrowserHttpError, match="error page"):
        await fetch_html_via_cloakbrowser(
            "https://source.example/missing",
            cdp_url="http://127.0.0.1:9222",
            allowed_hosts={"source.example"},
            wait_seconds=0,
        )
