"""CloakBrowser-backed search clients for FSSP and KAD.

The legacy helper in ``Hermes_projects`` used the synchronous CloakBrowser API
inside ``async`` methods and attempted to solve CAPTCHA automatically.  This
module keeps its useful public contract while using the project's async CDP
transport, per-source broker and manual challenge policy.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from src.config import get_settings
from src.connectors.cloakbrowser import (
    CHALLENGE_MARKERS,
    BrowserResult,
    BrowserState,
    _safe_close,
    _safe_close_page,
    get_browser_session_broker,
)


class CaseType(Enum):
    """Compatibility labels from the original government parser."""

    EXECUTIVE_PAYMENT = "executive_payment"
    ARBITRATION = "arbitration"


@dataclass(frozen=True)
class ExecutiveSearchResult:
    number: str
    date: str
    region: str
    creditor: str
    debtor: str
    amount: float
    status: str
    link: str = ""


@dataclass(frozen=True)
class ArbitrationCaseResult:
    number: str
    date: str
    court: str
    plaintiff: str
    defendant: str
    status: str
    link: str = ""


@dataclass(frozen=True)
class ParsResult:
    source: str
    cases: list[ExecutiveSearchResult | ArbitrationCaseResult]


def _status_for_http(status: int | None) -> BrowserState:
    if status == 451:
        return BrowserState.ROUTE_BLOCKED
    if status in (401, 403, 429):
        return BrowserState.CHALLENGE
    if status == 404:
        return BrowserState.NOT_FOUND
    return BrowserState.HTTP_ERROR


def _money(value: str) -> float:
    cleaned = re.sub(r"[^0-9,.-]", "", value.replace("\u00a0", ""))
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(",") > 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", "")
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return 0.0


async def _body_text(page: Any) -> str:
    try:
        return await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


async def _first_locator(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count() > 0:
            return locator.first
    return None


async def _text(locator: Any) -> str:
    try:
        return (await locator.inner_text()).strip()
    except Exception:
        return ""


class GovernmentParser:
    """Unified parser compatible with the legacy ``parser.fssp``/``parser.kad`` API."""

    def __init__(
        self,
        proxy: str | None = None,
        profile_dir: str | None = None,
        *,
        cdp_url: str | None = None,
        timeout_seconds: int | None = None,
        manual_challenge_wait_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.cdp_url = cdp_url or getattr(settings, "cloakbrowser_cdp_url", "")
        self.proxy = proxy
        self.profile_dir = profile_dir
        self.timeout_seconds = timeout_seconds or int(
            getattr(settings, "cloakbrowser_timeout_seconds", 90)
        )
        self.manual_challenge_wait_seconds = (
            manual_challenge_wait_seconds
            if manual_challenge_wait_seconds is not None
            else int(getattr(settings, "fssp_manual_challenge_wait_seconds", 300))
        )
        self.min_interval_seconds = float(
            getattr(settings, "cloakbrowser_min_interval_seconds", 2.0)
        )
        self.last_results: dict[str, BrowserResult] = {}
        self._playwright: Any = None
        self._playwright_context: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}
        self.fssp = FSSPClient(self)
        self.kad = KADClient(self)

    async def _get_browser(self) -> Any:
        if self._browser is not None and getattr(self._browser, "is_connected", lambda: True)():
            return self._browser
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]

        factory = async_playwright()
        if hasattr(factory, "start"):
            self._playwright = await factory.start()
        else:  # test doubles and older clients expose only the async context API
            self._playwright_context = factory
            self._playwright = await factory.__aenter__()
        self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
        return self._browser

    async def _reset_browser(self) -> None:
        if self._browser is not None and hasattr(self._browser, "close"):
            await _safe_close(self._browser)
        self._browser = None
        if self._playwright is not None and hasattr(self._playwright, "stop"):
            try:
                await self._playwright.stop()
            except Exception:
                pass
        if self._playwright_context is not None:
            try:
                await self._playwright_context.__aexit__(None, None, None)
            except Exception:
                pass
        self._playwright = None
        self._playwright_context = None

    async def _run_page(
        self,
        source: str,
        url: str,
        operation: Callable[[Any], Awaitable[BrowserResult]],
        *,
        allowed_hosts: tuple[str, ...],
    ) -> BrowserResult:
        if not self.cdp_url:
            result = BrowserResult(
                BrowserState.UNAVAILABLE,
                url=url,
                error="CLOAKBROWSER_CDP_URL is not configured",
            )
            self.last_results[source] = result
            return result
        try:
            browser = await self._get_browser()
        except ImportError:
            result = BrowserResult(
                BrowserState.UNAVAILABLE,
                url=url,
                error="CloakBrowser fallback requires the optional playwright package",
            )
            self.last_results[source] = result
            return result

        page = None
        try:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = self._pages.get(source)
            reused_page = page is not None and not getattr(page, "is_closed", lambda: False)()
            if page is None or getattr(page, "is_closed", lambda: False)():
                page = await context.new_page()
                self._pages[source] = page
            elif reused_page:
                try:
                    await page.goto("about:blank", wait_until="commit", timeout=2000)
                except Exception:
                    pass
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=max(1, self.timeout_seconds) * 1000,
            )
            status = getattr(response, "status", None)
            final_url = getattr(page, "url", url)
            final_host = (urlparse(final_url).hostname or "").lower()
            if final_host not in allowed_hosts:
                result = BrowserResult(
                    BrowserState.UNAVAILABLE,
                    status_code=status,
                    url=final_url,
                    error="browser navigation left the source allowlist",
                )
            elif isinstance(status, int) and status >= 400:
                result = BrowserResult(
                    _status_for_http(status),
                    status_code=status,
                    url=final_url,
                    error=f"browser navigation status={status}",
                )
            else:
                body = await _body_text(page)
                if any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
                    deadline = self.manual_challenge_wait_seconds
                    while deadline > 0 and any(
                        marker in body[:5000].lower() for marker in CHALLENGE_MARKERS
                    ):
                        await page.wait_for_timeout(2000)
                        deadline -= 2
                        body = await _body_text(page)
                    if any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
                        result = BrowserResult(
                            BrowserState.CHALLENGE,
                            status_code=status,
                            url=final_url,
                            error="browser challenge is still visible",
                        )
                    else:
                        result = await operation(page)
                else:
                    result = await operation(page)
        except Exception as exc:
            result = BrowserResult(
                BrowserState.HTTP_ERROR,
                url=url,
                error=f"government browser workflow failed: {type(exc).__name__}",
            )
        finally:
            if result.state is BrowserState.HTTP_ERROR and page is not None:
                try:
                    await page.goto("about:blank", wait_until="commit", timeout=2000)
                except Exception:
                    pass
        self.last_results[source] = result
        return result

    async def _execute(
        self,
        source: str,
        url: str,
        operation: Callable[[Any], Awaitable[BrowserResult]],
        *,
        allowed_hosts: tuple[str, ...],
    ) -> BrowserResult:
        return await get_browser_session_broker().execute(
            source,
            lambda: self._run_page(source, url, operation, allowed_hosts=allowed_hosts),
            min_interval_seconds=self.min_interval_seconds,
        )

    async def close(self) -> None:
        """Disconnect is owned by the CloakBrowser process; pages are short-lived."""
        for page in tuple(self._pages.values()):
            await _safe_close_page(page)
        self._pages.clear()
        await self._reset_browser()


class FSSPClient:
    BASE_URL = "https://fssp.gov.ru/iss/ip/"
    LIVE_HOST = "https://is-go.fssp.gov.ru/"

    def __init__(
        self,
        parser: GovernmentParser | None = None,
        proxy: str | None = None,
        profile_dir: str | None = None,
        *,
        cdp_url: str | None = None,
    ):
        self.parser = parser or GovernmentParser(
            proxy=proxy, profile_dir=profile_dir, cdp_url=cdp_url
        )

    async def _search(self, mode: str, values: dict[str, str], region: str) -> BrowserResult:
        async def operation(page: Any) -> BrowserResult:
            mode_locator = await _first_locator(
                page,
                (f'input[value="{mode}"]', f'input[name="search_type"][value="{mode}"]'),
            )
            if mode_locator is not None:
                await mode_locator.click()
            for name, value in values.items():
                locator = await _first_locator(
                    page,
                    (f'input[name="is[{name}]"]', f'input[name="{name}"]'),
                )
                if locator is None:
                    return BrowserResult(BrowserState.UNAVAILABLE, error=f"FSSP field not found: {name}")
                await locator.fill(value)
            region_locator = await _first_locator(page, ("select#region_id", "select[name='region']"))
            if region_locator is not None:
                await region_locator.select_option(label=region)
            button = await _first_locator(page, ("#btn-sbm", "button[type='submit']", "input[type='submit']"))
            if button is None:
                return BrowserResult(BrowserState.UNAVAILABLE, error="FSSP submit selector was not found")
            await button.click(timeout=max(1, self.parser.timeout_seconds) * 1000)
            await page.wait_for_timeout(1000)
            body = await _body_text(page)
            if re.search(r"\b(?:502|503|504)\b|temporarily unavailable|временно недоступ", body, re.IGNORECASE):
                return BrowserResult(
                    BrowserState.HTTP_ERROR,
                    status_code=503,
                    body=body,
                    error="FSSP returned a temporary service error",
                )
            if any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
                deadline = self.parser.manual_challenge_wait_seconds
                while deadline > 0 and any(
                    marker in body[:5000].lower() for marker in CHALLENGE_MARKERS
                ):
                    await page.wait_for_timeout(2000)
                    deadline -= 2
                    body = await _body_text(page)
                if any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
                    return BrowserResult(
                        BrowserState.CHALLENGE,
                        status_code=200,
                        body=body,
                        error="FSSP CAPTCHA is still visible",
                    )
            rows = page.locator(".executive_proceedings_table tbody tr")
            if await rows.count() == 0:
                if re.search(r"не найден|не обнаружен|отсутствуют", body, re.IGNORECASE) or re.search(
                    r'"(?:result|data|items)"\s*:\s*\[\s*\]', body, re.IGNORECASE
                ):
                    return BrowserResult(BrowserState.READY, status_code=200, body="[]")
                return BrowserResult(BrowserState.HTTP_ERROR, status_code=200, body=body, error="FSSP result table was not found")
            records: list[dict[str, object]] = []
            for index in range(await rows.count()):
                cells = rows.nth(index).locator("td")
                if await cells.count() < 6:
                    continue
                values_text = [await _text(cells.nth(cell)) for cell in range(await cells.count())]
                link = ""
                anchor = rows.nth(index).locator("a")
                if await anchor.count() > 0:
                    link = (await anchor.first.get_attribute("href")) or ""
                amount_index = 5 if len(values_text) > 6 else 4
                status_index = 6 if len(values_text) > 6 else 5
                records.append(
                    asdict(
                        ExecutiveSearchResult(
                            number=values_text[0],
                            date=values_text[1],
                            region=values_text[2],
                            creditor=values_text[3],
                            debtor=values_text[4] if len(values_text) > 6 else "",
                            amount=_money(values_text[amount_index]),
                            status=values_text[status_index],
                            link=link,
                        )
                    )
                )
            return BrowserResult(BrowserState.READY, status_code=200, body=json.dumps(records, ensure_ascii=False))

        return await self.parser._execute(
            "fssp",
            self.BASE_URL,
            operation,
            allowed_hosts=("fssp.gov.ru", "is-go.fssp.gov.ru"),
        )

    @staticmethod
    def _decode(result: BrowserResult) -> list[ExecutiveSearchResult]:
        if not result.ok:
            return []
        try:
            return [ExecutiveSearchResult(**item) for item in json.loads(result.body)]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    async def search_by_ip_number(self, ip_number: str, region: str = "Москва") -> list[ExecutiveSearchResult]:
        result = await self._search("3", {"ip_number": ip_number}, region)
        return self._decode(result)

    async def search_by_person(
        self,
        last_name: str,
        first_name: str,
        birth_date: str,
        region: str,
    ) -> list[ExecutiveSearchResult]:
        result = await self._search(
            "1",
            {"last_name": last_name, "first_name": first_name, "date": birth_date},
            region,
        )
        return self._decode(result)


class KADClient:
    BASE_URL = "https://kad.arbitr.ru/"

    def __init__(
        self,
        parser: GovernmentParser | None = None,
        proxy: str | None = None,
        profile_dir: str | None = None,
        *,
        cdp_url: str | None = None,
    ):
        self.parser = parser or GovernmentParser(
            proxy=proxy, profile_dir=profile_dir, cdp_url=cdp_url
        )

    async def search_cases(
        self,
        query: str,
        court: str | None = None,
        date_from: str | None = None,
    ) -> list[ArbitrationCaseResult]:
        payload = {
            "Count": 50,
            "Page": 1,
            "Courts": [court] if court else [],
            "DateFrom": date_from or "",
            "DateTo": "",
            "Sides": [{"Name": query, "Type": 0, "INN": "", "OGRN": ""}],
            "Judges": [],
            "CaseNumbers": [],
            "Hearings": [],
        }

        async def operation(page: Any) -> BrowserResult:
            raw = await page.evaluate(
                """async ({path, payload}) => {
                    const response = await fetch(path, {method: 'POST', credentials: 'include',
                      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
                      body: JSON.stringify(payload)});
                    return {status: response.status, url: response.url, text: await response.text()};
                }""",
                {"path": "/Kad/SearchInstances", "payload": payload},
            )
            status = raw.get("status") if isinstance(raw, dict) else None
            text = raw.get("text", "") if isinstance(raw, dict) else ""
            response_url = raw.get("url") if isinstance(raw, dict) else None
            if not isinstance(status, int):
                return BrowserResult(BrowserState.HTTP_ERROR, url=response_url, error="KAD status is invalid")
            if status >= 400:
                return BrowserResult(_status_for_http(status), status_code=status, url=response_url, error=f"KAD status={status}")
            return BrowserResult(BrowserState.READY, status_code=status, url=response_url, body=text)

        result = await self.parser._execute(
            "kad", self.BASE_URL, operation, allowed_hosts=("kad.arbitr.ru",)
        )
        return self._decode(result)

    @staticmethod
    def _decode(result: BrowserResult) -> list[ArbitrationCaseResult]:
        if not result.ok:
            return []
        try:
            payload = json.loads(result.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return KADClient._decode_html(result.body)
        root = payload.get("Result") or payload.get("result") or payload
        items = root.get("Items") or root.get("items") or root.get("results") if isinstance(root, dict) else None
        if not isinstance(items, list):
            return KADClient._decode_html(result.body)
        records: list[ArbitrationCaseResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            parties = item.get("Parties") or item.get("parties") or []
            plaintiff = str(item.get("Plaintiff") or item.get("plaintiff") or (parties[0] if isinstance(parties, list) and parties else ""))
            defendant = str(item.get("Defendant") or item.get("defendant") or (parties[1] if isinstance(parties, list) and len(parties) > 1 else ""))
            href = str(item.get("Url") or item.get("url") or item.get("Link") or item.get("link") or "")
            records.append(
                ArbitrationCaseResult(
                    number=str(item.get("CaseNumber") or item.get("caseNumber") or item.get("Number") or item.get("number") or ""),
                    date=str(item.get("Date") or item.get("date") or ""),
                    court=str(item.get("Court") or item.get("court") or ""),
                    plaintiff=plaintiff,
                    defendant=defendant,
                    status=str(item.get("CaseStatus") or item.get("caseStatus") or item.get("Status") or item.get("status") or ""),
                    link=urljoin(KADClient.BASE_URL, href),
                )
            )
        return records

    @staticmethod
    def _decode_html(html: str) -> list[ArbitrationCaseResult]:
        """Fallback for public KAD HTML shells or older frontend versions."""
        tree = HTMLParser(html)
        records: list[ArbitrationCaseResult] = []
        # ``.case-list .case-item`` and ``.case-item`` overlap in selectolax
        # and return the same node twice.  Keep one selector per row shape.
        for row in tree.css(".case-item, tr"):
            text = row.text(separator=" ").strip()
            match = re.search(r"[А-ЯA-Z]\d{1,3}-\d+/\d{4}", text)
            if not match:
                continue
            cells = [cell.text().strip() for cell in row.css("td")]
            links = row.css("a")
            href = links[0].attributes.get("href", "") if links else ""
            records.append(
                ArbitrationCaseResult(
                    number=match.group(0),
                    date=cells[1] if len(cells) > 1 else "",
                    court=cells[2] if len(cells) > 2 else "",
                    plaintiff=cells[3] if len(cells) > 3 else "",
                    defendant=cells[4] if len(cells) > 4 else "",
                    status=cells[5] if len(cells) > 5 else "",
                    link=urljoin(KADClient.BASE_URL, href),
                )
            )
        return records
