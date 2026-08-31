"""Enrich-воркер — обогащение дебитора данными из открытых реестров."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urlparse

import httpx
from selectolax.parser import HTMLParser

from src.config import get_settings
from src.connectors.cloakbrowser import (
    CHALLENGE_MARKERS,
    CloakBrowserError,
    fetch_html_via_cloakbrowser,
)
from src.connectors.providers import provider_api_enabled

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.entities import Party


logger = logging.getLogger(__name__)
EGRUL_PUBLIC_SEARCH_URL = "https://egrul.nalog.ru/"


async def _fetch_egrul_rows(inn: str) -> list[dict[str, object]] | None:
    """Submit the FNS public web form and parse its short-lived result page.

    The form is part of the public EGRUL interface, not a configurable third-
    party API: it needs neither a token nor a paid account.  A CAPTCHA is left
    for the configured CloakBrowser fallback below.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            search = await client.post(
                EGRUL_PUBLIC_SEARCH_URL,
                data={"query": inn, "region": "", "PreventChromeAutocomplete": ""},
            )
            search.raise_for_status()
            payload = search.json()
            if not isinstance(payload, dict) or payload.get("captchaRequired"):
                return None
            token = payload.get("t")
            if not isinstance(token, str) or not token:
                return None

            result_url = f"{EGRUL_PUBLIC_SEARCH_URL}search-result/{token}"
            for delay in (0.0, 0.5, 1.0):
                if delay:
                    await asyncio.sleep(delay)
                result = await client.get(result_url)
                result.raise_for_status()
                result_payload = result.json()
                if not isinstance(result_payload, dict) or result_payload.get("captchaRequired"):
                    return None
                rows = result_payload.get("rows")
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
    except (httpx.HTTPError, ValueError, TypeError):
        logger.exception("EGRUL public search failed for INN %s", inn)
    return None


def _egrul_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if "ликвид" in lowered:
        return "liquidation"
    if "исключ" in lowered:
        return "excluded"
    if "банкрот" in lowered:
        return "bankruptcy"
    if any(marker in lowered for marker in ("действующ", "действует", "зарегистрировано")):
        return "active"
    return None


def _apply_egrul_row(party: Party, row: dict[str, object]) -> bool:
    """Apply a validated FNS search row, including status when supplied."""
    name = row.get("n")
    ogrn = row.get("o")
    parsed_name = name.strip() if isinstance(name, str) else ""
    parsed_ogrn = ogrn.strip() if isinstance(ogrn, str) and ogrn.isdigit() else ""
    if not parsed_name and not parsed_ogrn:
        return False
    if parsed_name and not party.name:
        party.name = parsed_name
    if parsed_ogrn and not party.ogrn:
        party.ogrn = parsed_ogrn
    for key in ("s", "status", "state", "c", "r"):
        parsed_status = _egrul_status(row.get(key))
        if parsed_status:
            party.status = parsed_status
            break
    party.source_as_of = datetime.now(UTC)
    return True


def _is_challenge(status_code: int, body: str) -> bool:
    lowered = body[:5000].lower()
    return status_code in (401, 403, 429) or any(marker in lowered for marker in CHALLENGE_MARKERS)


async def _fetch_html(
    url: str,
    *,
    method: str = "GET",
    json_payload: dict | None = None,
) -> str | None:
    """Fetch a parser page and retry a challenge through CloakBrowser."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            if method == "POST":
                resp = await client.post(url, json=json_payload or {})
            else:
                resp = await client.get(url)
            initial_host = (urlparse(url).hostname or "").lower()
            final_host = (urlparse(str(resp.url)).hostname or "").lower()
            if final_host != initial_host:
                logger.warning("source parser redirect left host allowlist: %s -> %s", url, resp.url)
                return None
            if not _is_challenge(resp.status_code, resp.text):
                resp.raise_for_status()
                return resp.text
    except Exception:
        # A DNS/transport failure is one of the supported reasons to switch
        # to the already authenticated CloakBrowser profile.  Do not return
        # before reaching the fallback below.
        logger.exception("source parser request failed: %s", url)

    cdp_url = getattr(settings, "cloakbrowser_cdp_url", "")
    if not cdp_url:
        return None
    host = urlparse(url).hostname or ""
    try:
        return await fetch_html_via_cloakbrowser(
            url,
            cdp_url=cdp_url,
            timeout_seconds=int(getattr(settings, "cloakbrowser_timeout_seconds", 90)),
            wait_seconds=int(getattr(settings, "cloakbrowser_wait_seconds", 8)),
            allowed_hosts={host},
        )
    except CloakBrowserError:
        logger.exception("source parser browser fallback failed: %s", url)
        return None


def _d(value: str | None, default: Decimal = Decimal(0)) -> Decimal:
    if not value:
        return default
    try:
        return Decimal(re.sub(r"[^\d.,]", "", value).replace(",", "."))
    except Exception:
        return default


async def enrich_from_egrul(party: Party, session: AsyncSession) -> bool:
    """Enrich from the official FNS EGRUL public interface."""
    if not party.inn:
        return False

    inn = party.inn
    url = f"https://egrul.nalog.ru/index.html?query={quote_plus(inn)}"

    try:
        row_applied = False
        rows = await _fetch_egrul_rows(inn)
        if rows:
            exact_row = next(
                (row for row in rows if str(row.get("i", "")) == inn), rows[0]
            )
            row_applied = _apply_egrul_row(party, exact_row)
            if row_applied and party.status is not None:
                return True

        # The browser path is retained as the CAPTCHA fallback.  It also
        # supports deployments where the FNS changes the JSON response but
        # keeps a rendered public result card.
        html = await _fetch_html(url)
        if not html:
            return row_applied
        lowered = html.lower()
        tree = HTMLParser(html)

        # Extract only source-specific organization markers. A generic page
        # heading (for example, "Поиск ЕГРЮЛ") is not an organization record.
        name_el = tree.css_first(
            ".org-name, [data-org-name], [itemprop='legalName'], .vyp-short-info__name, h1[data-inn]"
        )
        parsed_name = name_el.text().strip() if name_el else ""

        # Parse status into a local value so an old ORM status cannot turn an
        # unrelated response into a successful fresh verification.
        parsed_status: str | None = None
        if "ликвидирована" in lowered:
            parsed_status = "liquidation"
        elif "исключена" in lowered:
            parsed_status = "excluded"
        elif "банкротств" in lowered:
            parsed_status = "bankruptcy"
        elif any(marker in lowered for marker in ("действующая", "действует", "зарегистрировано")):
            parsed_status = "active"

        # ОГРН
        ogrn_m = re.search(r"ОГРН[:\s]*(\d{13})", html)
        # A status phrase by itself can appear in search filters/help text.
        # Require an organization identity marker before accepting the page.
        success = bool(parsed_name or ogrn_m)
        if success:
            if parsed_name and not party.name:
                party.name = parsed_name
            if parsed_status:
                party.status = parsed_status
            if ogrn_m and not party.ogrn:
                party.ogrn = ogrn_m.group(1)
            party.source_as_of = datetime.now(UTC)
        return success or row_applied
    except Exception:
        logger.exception("egrul enrichment failed for %s", party.inn)
        return False


async def enrich_from_fssp(party: Party, session: AsyncSession) -> bool:
    """Запрашивает ФССП исполнительные производства по ИНН."""
    if not party.inn:
        return False

    try:
        source_settings = get_settings()
        fssp_api_url = getattr(source_settings, "fssp_api_url", "https://api-ip.fssprus.ru")
        if provider_api_enabled(
            "fssp", source_settings.free_api_sources_list, fssp_api_url
        ):
            endpoint = "legal" if len(party.inn) == 10 else "physical"
            url = f"{fssp_api_url.rstrip('/')}/api/v1.0/search/{endpoint}"
            params = {"query": party.inn}
            if source_settings.fssp_api_token:
                params["token"] = source_settings.fssp_api_token
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    return False
                data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("result"), list):
                return False
            results = data["result"]
            total_sum = sum((_d(item.get("sum", "")) for item in results), Decimal(0))
            party.fssp_sum = total_sum
            party.fssp_uncollectible = any(
                item.get("status") == "uncollectible" for item in results
            )
            return True

        html = await _fetch_html(f"https://fssp.gov.ru/iss/ip?query={quote_plus(party.inn)}")
        if not html:
            return False
        lowered = html.lower()
        if not re.search(r"производств(?:а)?\s+не\s+найден", lowered) and (
            "исполнительн" not in lowered
            or not re.search(r"(?:руб|₽|сумм|результат|найдено)", lowered)
        ):
            return False
        sums = [_d(value) for value in re.findall(r"\d[\d\s\u00a0.,]*\s*(?:руб|₽)", html, re.IGNORECASE)]
        party.fssp_sum = sum(sums, Decimal(0))
        party.fssp_uncollectible = any(
            marker in lowered for marker in ("п. 4 ч. 1 ст. 46", "невозможностью взыскания")
        )
        return True
    except Exception:
        logger.exception("fssp enrichment failed for %s", party.inn)
        return False


async def enrich_from_kad(party: Party, session: AsyncSession) -> bool:
    """Запрашивает КАД арбитражные дела по ИНН."""
    if not party.inn:
        return False

    try:
        url = "https://kad.arbitr.ru/Kad/SearchInstances"
        payload = {
            "Count": 5,
            "Page": 1,
            "Courts": [],
            "DateFrom": "",
            "DateTo": "",
            "Sides": [{"Name": party.name or "", "Type": 0, "INN": party.inn, "OGRN": ""}],
            "Judges": [],
            "CaseNumbers": [],
            "Hearings": [],
        }
        if provider_api_enabled("kad", get_settings().free_api_sources_list, url):
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return False
                data = resp.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("total"), int)
                or not isinstance(data.get("results"), list)
            ):
                return False
            party.kad_as_defendant_count = data["total"]
            party.kad_bankruptcy_open = any(
                "банкротств" in str(d).lower() for d in data.get("results", [])
            )
            return True

        html = await _fetch_html(f"https://kad.arbitr.ru/Kad/SearchInstances?Sides={quote_plus(party.inn)}")
        if not html:
            return False
        lowered = html.lower()
        tree = HTMLParser(html)
        count_match = re.search(r"(?:найдено|всего|дел[ао])\D{0,30}(\d+)", lowered)
        if count_match:
            party.kad_as_defendant_count = int(count_match.group(1))
        elif "дел не найдено" in lowered or "ничего не найдено" in lowered:
            party.kad_as_defendant_count = 0
        else:
            return False
        # Only inspect actual case rows.  Searching the whole page turns a
        # navigation label such as "банкротство" into a false positive.
        case_texts = []
        for node in tree.css("tr, .b-case, .case-item, [data-case-number]"):
            node_text = node.text().strip().lower()
            if re.search(r"[а-яa-z]\d{1,3}-\d+/\d{4}", node_text):
                case_texts.append(node_text)
        party.kad_bankruptcy_open = any("банкрот" in text for text in case_texts)
        return True
    except Exception:
        logger.exception("kad enrichment failed for %s", party.inn)
        return False


async def enrich_party(party: Party, session: AsyncSession) -> dict[str, bool]:
    """Обогащает лицо без конкурентных commit одной SQLAlchemy-сессии."""
    return {
        "egrul": await enrich_from_egrul(party, session),
        "fssp": await enrich_from_fssp(party, session),
        "kad": await enrich_from_kad(party, session),
    }
