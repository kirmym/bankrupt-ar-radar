"""Unit coverage for typed browser transport and source workflows."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.connectors.cloakbrowser import (
    BrowserResult,
    BrowserSessionBroker,
    BrowserState,
    browser_metrics_snapshot,
    fetch_fssp_via_cloakbrowser,
    fetch_json_via_cloakbrowser,
    reset_browser_metrics,
)


class _Locator:
    def __init__(self, page: _FsspPage, selector: str):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self) -> int:
        return int(self.selector in {"#input10", "#btn-sbm", "body"})

    async def fill(self, value: str) -> None:
        self.page.filled = value

    async def click(self, **_kwargs) -> None:
        self.page.clicked = True

    async def inner_text(self, **_kwargs) -> str:
        return self.page.body


class _FsspPage:
    url = "https://fssp.gov.ru/iss/ip/"

    def __init__(self, body: str):
        self.body = body
        self.filled: str | None = None
        self.clicked = False

    async def goto(self, *_args, **_kwargs):
        return SimpleNamespace(status=200, url=self.url)

    def locator(self, selector: str) -> _Locator:
        return _Locator(self, selector)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def close(self) -> None:
        return None


class _JsonPage:
    url = "https://kad.arbitr.ru/"

    async def goto(self, *_args, **_kwargs):
        return SimpleNamespace(status=200, url=self.url)

    async def evaluate(self, _script: str, _argument: dict[str, object]):
        return {
            "status": 200,
            "url": "https://kad.arbitr.ru/Kad/SearchInstances",
            "text": json.dumps({"Result": {"TotalCount": 1, "Items": [{"CaseType": "Б"}]}}),
        }

    async def close(self) -> None:
        return None


class _Context:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class _Browser:
    def __init__(self, page):
        self.contexts = [_Context(page)]


class _Chromium:
    def __init__(self, browser):
        self.browser = browser

    async def connect_over_cdp(self, _cdp_url: str):
        return self.browser


class _Playwright:
    def __init__(self, browser):
        self.chromium = _Chromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_browser_session_broker_records_route_blocked_metric() -> None:
    reset_browser_metrics()
    broker = BrowserSessionBroker()

    async def operation() -> BrowserResult:
        await asyncio.sleep(0)
        return BrowserResult(BrowserState.ROUTE_BLOCKED, status_code=451)

    result = await broker.execute("kad", operation)

    assert result.state is BrowserState.ROUTE_BLOCKED
    metrics = browser_metrics_snapshot()
    assert metrics["requests"] == 1
    assert metrics["states"]["route_blocked"] == 1


@pytest.mark.asyncio
async def test_kad_json_request_runs_same_origin_post(monkeypatch: pytest.MonkeyPatch) -> None:
    import playwright.async_api as playwright_api

    page = _JsonPage()
    monkeypatch.setattr(
        playwright_api,
        "async_playwright",
        lambda: _Playwright(_Browser(page)),
    )

    result = await fetch_json_via_cloakbrowser(
        "https://kad.arbitr.ru/",
        path="/Kad/SearchInstances",
        payload={"Sides": [{"INN": "7707083893"}]},
        cdp_url="http://127.0.0.1:9222",
        allowed_hosts={"kad.arbitr.ru"},
    )

    assert result.ok is True
    assert result.state is BrowserState.READY
    assert '"TotalCount": 1' in result.body
    assert result.url == "https://kad.arbitr.ru/Kad/SearchInstances"


@pytest.mark.asyncio
async def test_fssp_workflow_fills_form_and_surfaces_manual_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import playwright.async_api as playwright_api

    page = _FsspPage("Введите CAPTCHA для продолжения")
    monkeypatch.setattr(
        playwright_api,
        "async_playwright",
        lambda: _Playwright(_Browser(page)),
    )

    result = await fetch_fssp_via_cloakbrowser(
        "7707083893",
        cdp_url="http://127.0.0.1:9222",
        manual_challenge_wait_seconds=0,
    )

    assert result.state is BrowserState.CHALLENGE
    assert page.filled == "7707083893"
    assert page.clicked is True
    assert "CAPTCHA" in (result.error or "")
