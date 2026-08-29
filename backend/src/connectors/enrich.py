"""Enrich-воркер — обогащение дебитора данными из открытых реестров."""
from __future__ import annotations

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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.entities import Party


EGRUL_API = "https://egrul.nic.ru"
FSSP_API = "https://api-ip.fssprus.ru"
KAD_API = "https://bssys.com"
logger = logging.getLogger(__name__)


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
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            if method == "POST":
                resp = await client.post(url, json=json_payload or {})
            else:
                resp = await client.get(url)
            if not _is_challenge(resp.status_code, resp.text):
                resp.raise_for_status()
                return resp.text
    except Exception:
        logger.exception("source parser request failed: %s", url)
        return None

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
    """Enrich EGRUL data via the free API when enabled, otherwise HTML parser."""
    if not party.inn:
        return False

    inn = party.inn
    url = f"https://egrul.nic.ru/search/?q={inn}&type=ul"

    try:
        html = await _fetch_html(url)
        if not html:
            return False
        lowered = html.lower()
        tree = HTMLParser(html)

        # Extract only source-specific organization markers. A generic page
        # heading (for example, "Поиск ЕГРЮЛ") is not an organization record.
        name_el = tree.css_first(
            ".org-name, [data-org-name], [itemprop='legalName'], h1[data-inn]"
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
        return success
    except Exception:
        logger.exception("egrul enrichment failed for %s", party.inn)
        return False


async def enrich_from_fssp(party: Party, session: AsyncSession) -> bool:
    """Запрашивает ФССП исполнительные производства по ИНН."""
    if not party.inn:
        return False

    try:
        source_settings = get_settings()
        if "fssp" in source_settings.free_api_sources_list:
            endpoint = "legal" if len(party.inn) == 10 else "physical"
            url = f"{source_settings.fssp_api_url.rstrip('/')}/api/v1.0/search/{endpoint}"
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
        if "исполнительн" not in lowered and "производств не найдено" not in lowered:
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
        url = "https://kad.arbitr.ru/Kad/SearchCases"
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
        if "kad" in get_settings().free_api_sources_list:
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

        html = await _fetch_html(f"https://kad.arbitr.ru/Kad/SearchCases?Sides={quote_plus(party.inn)}")
        if not html:
            return False
        lowered = html.lower()
        count_match = re.search(r"(?:найдено|всего|дел[ао])\D{0,30}(\d+)", lowered)
        if count_match:
            party.kad_as_defendant_count = int(count_match.group(1))
        elif "дел не найдено" in lowered or "ничего не найдено" in lowered:
            party.kad_as_defendant_count = 0
        else:
            return False
        party.kad_bankruptcy_open = "банкрот" in lowered
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
