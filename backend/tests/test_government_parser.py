"""Contract tests for the GovernmentParser port."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.connectors.cloakbrowser import BrowserResult, BrowserState
from src.connectors.government import GovernmentParser, KADClient


class _BodyLocator:
    def __init__(self, page: _Page):
        self.page = page
        self.first = self

    async def count(self) -> int:
        return 1

    async def inner_text(self, **_kwargs) -> str:
        return self.page.body


class _Cell:
    def __init__(self, value: str):
        self.value = value

    async def inner_text(self, **_kwargs) -> str:
        return self.value

    async def get_attribute(self, _name: str):
        return None


class _Row:
    def __init__(self):
        self.values = ["123-1/2026", "01.01.2026", "Москва", "ООО Кредитор", "ООО Должник", "1 234,50", "Открыто"]

    def locator(self, selector: str):
        if selector == "td":
            return _Cells(self.values)
        return _Anchor()


class _Cells:
    def __init__(self, values: list[str]):
        self.values = values

    async def count(self) -> int:
        return len(self.values)

    def nth(self, index: int):
        return _Cell(self.values[index])


class _Anchor:
    first = None

    async def count(self) -> int:
        return 0


class _Rows:
    def __init__(self):
        self.row = _Row()

    async def count(self) -> int:
        return 1

    def nth(self, _index: int):
        return self.row


class _Page:
    url = "https://fssp.gov.ru/iss/ip/"

    def __init__(self, *, kad: bool = False):
        self.kad = kad
        self.body = "Результаты поиска"

    async def goto(self, *_args, **_kwargs):
        return SimpleNamespace(status=200, url=self.url)

    def locator(self, selector: str):
        if selector == "body":
            return _BodyLocator(self)
        if selector == ".executive_proceedings_table tbody tr":
            return _Rows()
        if selector in {"#btn-sbm", 'input[value="3"]', 'input[name="is[ip_number]"]'}:
            return _Input(self, selector)
        return _EmptyLocator()

    async def evaluate(self, _script: str, _argument: dict[str, object]):
        return {
            "status": 200,
            "url": "https://kad.arbitr.ru/Kad/SearchInstances",
            "text": json.dumps({"Result": {"TotalCount": 1, "Items": [{"CaseNumber": "А40-1/2026", "Court": "АС Москвы"}]}}),
        }

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def close(self) -> None:
        return None


class _Input:
    def __init__(self, page: _Page, selector: str):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self) -> int:
        return 1

    async def click(self, **_kwargs) -> None:
        return None

    async def fill(self, _value: str) -> None:
        return None

    async def select_option(self, **_kwargs) -> None:
        return None


class _EmptyLocator(_Input):
    def __init__(self):
        self.first = self

    async def count(self) -> int:
        return 0


class _Context:
    def __init__(self, page: _Page):
        self.page = page

    async def new_page(self):
        return self.page


class _Browser:
    def __init__(self, page: _Page):
        self.contexts = [_Context(page)]


class _Chromium:
    def __init__(self, browser: _Browser):
        self.browser = browser

    async def connect_over_cdp(self, _url: str):
        return self.browser


class _Playwright:
    def __init__(self, browser: _Browser):
        self.chromium = _Chromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_fssp_ip_search_normalizes_legacy_table(monkeypatch: pytest.MonkeyPatch) -> None:
    import playwright.async_api as playwright_api

    page = _Page()
    monkeypatch.setattr(playwright_api, "async_playwright", lambda: _Playwright(_Browser(page)))
    parser = GovernmentParser(cdp_url="http://127.0.0.1:9222", manual_challenge_wait_seconds=0)
    parser.min_interval_seconds = 0

    rows = await parser.fssp.search_by_ip_number("123-1/2026")

    assert len(rows) == 1
    assert rows[0].number == "123-1/2026"
    assert rows[0].amount == 1234.5
    assert parser.last_results["fssp"].ok is True


@pytest.mark.asyncio
async def test_kad_search_uses_same_origin_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import playwright.async_api as playwright_api

    page = _Page(kad=True)
    page.url = "https://kad.arbitr.ru/"
    monkeypatch.setattr(playwright_api, "async_playwright", lambda: _Playwright(_Browser(page)))
    parser = GovernmentParser(cdp_url="http://127.0.0.1:9222", manual_challenge_wait_seconds=0)
    parser.min_interval_seconds = 0

    rows = await parser.kad.search_cases("ООО Ромашка")

    assert len(rows) == 1
    assert rows[0].number == "А40-1/2026"
    assert rows[0].court == "АС Москвы"
    assert parser.last_results["kad"].ok is True


@pytest.mark.asyncio
async def test_fssp_ajax_503_body_is_not_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import playwright.async_api as playwright_api

    page = _Page()
    page.body = "503 Service Temporarily Unavailable"
    monkeypatch.setattr(playwright_api, "async_playwright", lambda: _Playwright(_Browser(page)))
    parser = GovernmentParser(cdp_url="http://127.0.0.1:9222", manual_challenge_wait_seconds=0)
    parser.min_interval_seconds = 0

    rows = await parser.fssp.search_by_ip_number("123-1/2026")

    assert rows == []
    assert parser.last_results["fssp"].state.value == "http_error"
    assert parser.last_results["fssp"].status_code == 503


def test_kad_html_fallback_extracts_case_rows() -> None:
    result = BrowserResult(
        BrowserState.READY,
        status_code=200,
        url="https://kad.arbitr.ru/",
        body=(
            '<div class="case-list"><div class="case-item">'
            '<span>A40-123/2024</span><span>01.02.2024</span>'
            '<span>Arbitration Court</span><span>Claimant LLC</span>'
            '<span>Respondent LLC</span><span>Closed</span>'
            '<a href="/Card/123">card</a>'
            "</div></div>"
        ),
    )

    rows = KADClient._decode(result)

    assert len(rows) == 1
    assert rows[0].number == "A40-123/2024"
    assert rows[0].link == "https://kad.arbitr.ru/Card/123"
