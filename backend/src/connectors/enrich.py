"""Enrich-воркер — обогащение дебитора данными из открытых реестров."""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urlparse

import httpx
from pypdf import PdfReader
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


async def _fetch_egrul_extract(row_token: str) -> str | None:
    """Download the official, free FNS EGRUL extract for a search row.

    The public site exposes a short-lived three-step contract.  It is not a
    paid API and can be rate limited, so every request is bounded and a
    challenge simply falls back to the normal CloakBrowser HTML path.
    """
    if not row_token:
        return None
    settings = get_settings()
    timeout = float(getattr(settings, "egrul_extract_timeout_seconds", 30))
    poll_seconds = max(0.1, float(getattr(settings, "egrul_extract_poll_seconds", 0.5)))
    max_polls = max(1, int(getattr(settings, "egrul_extract_max_polls", 8)))
    base_url = EGRUL_PUBLIC_SEARCH_URL.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            requested = await client.get(f"{base_url}/vyp-request/{row_token}")
            if requested.status_code in (401, 403, 429):
                return None
            requested.raise_for_status()
            request_payload = requested.json()
            if not isinstance(request_payload, dict) or request_payload.get("captchaRequired"):
                return None
            extract_token = request_payload.get("t") or row_token
            if not isinstance(extract_token, str) or not extract_token:
                return None

            for attempt in range(max_polls):
                status_response = await client.get(f"{base_url}/vyp-status/{extract_token}")
                if status_response.status_code in (401, 403, 429):
                    return None
                status_response.raise_for_status()
                status_payload = status_response.json()
                status = status_payload.get("status") if isinstance(status_payload, dict) else None
                if status == "ready":
                    break
                if status in {"error", "failed", "not_found"}:
                    return None
                if attempt + 1 < max_polls:
                    await asyncio.sleep(poll_seconds)
            else:
                return None

            document = await client.get(f"{base_url}/vyp-download/{extract_token}")
            if document.status_code in (401, 403, 429):
                return None
            document.raise_for_status()
            content_type = document.headers.get("content-type", "").lower()
            if "pdf" not in content_type or not document.content.startswith(b"%PDF"):
                return None
            if len(document.content) > 8 * 1024 * 1024:
                logger.warning("EGRUL extract is unexpectedly large: %d bytes", len(document.content))
                return None
            reader = PdfReader(io.BytesIO(document.content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text if text.strip() else None
    except (httpx.HTTPError, ValueError, TypeError, OSError):
        logger.warning("EGRUL extract download failed", exc_info=True)
        return None


def _parse_egrul_extract(text: str) -> dict[str, object]:
    """Extract only explicit adverse markers from a FNS PDF.

    Absence of an adverse marker is deliberately not interpreted as ``active``
    because the extract format does not provide a single authoritative status
    field in every version of the public form.
    """
    compact = re.sub(r"\s+", " ", text.lower()).strip()
    pending_exclusion = bool(
        re.search(r"предстоящ(?:ем|ее|его)\s+исключен|решени[ея]\s+о\s+предстоящем\s+исключ", compact)
    )
    invalid = "сведения недостовер" in compact or "недостоверности сведений" in compact
    invalid_address = bool(
        re.search(r"(?:адрес[^.]{0,120}недостовер|недостовер[^.]{0,120}адрес)", compact)
    )
    invalid_director = bool(
        re.search(
            r"(?:руководител|директор)[^.]{0,120}недостовер|недостовер[^.]{0,120}(?:руководител|директор)",
            compact,
        )
    )
    excluded = bool(
        re.search(r"исключен(?:о|а)?\s+из\s+егрюл|исключение\s+юридического\s+лица\s+заверш", compact)
    )
    liquidation = bool(
        re.search(
            r"(?<!не\s)находится\s+в\s+процессе\s+ликвидац|"
            r"ликвидационн(?:ая|ой)\s+комисс|ликвидатор",
            compact,
        )
    )
    bankruptcy = bool(re.search(r"процедур[аы]\s+банкротств|дело\s+о\s+банкротств", compact))

    status: str | None = None
    if excluded and not pending_exclusion:
        status = "excluded"
    elif liquidation:
        status = "liquidation"
    elif bankruptcy:
        status = "bankruptcy"
    elif invalid:
        status = "invalid"

    return {
        "status": status,
        "invalid_address": invalid_address,
        "invalid_director": invalid_director,
        "pending_exclusion": pending_exclusion,
    }


def _apply_egrul_extract(party: Party, parsed: dict[str, object]) -> bool:
    """Apply explicit risk flags from a validated FNS extract."""
    changed = False
    status = parsed.get("status")
    if isinstance(status, str) and status:
        party.status = status
        changed = True
    for field in ("invalid_address", "invalid_director", "pending_exclusion"):
        if parsed.get(field) is True:
            setattr(party, field, True)
            changed = True
    return changed


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
    settings = get_settings()

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

            # The short search card has no lifecycle status on the current
            # public contract.  Fetch the free official extract only when the
            # feature is enabled and the row contains its opaque token.
            if row_applied and getattr(settings, "egrul_extract_enabled", False):
                row_token = exact_row.get("t")
                if isinstance(row_token, str) and row_token:
                    extract_text = await _fetch_egrul_extract(row_token)
                    if extract_text:
                        _apply_egrul_extract(party, _parse_egrul_extract(extract_text))
                        party.source_as_of = datetime.now(UTC)
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
    # The connectors only mutate disjoint fields on the same in-memory party;
    # database writes remain serialized by the worker after all requests finish.
    # Running the independent public-source requests together prevents a slow
    # or blocked KAD/FSSP endpoint from delaying the EGRUL identity result.
    egrul, fssp, kad = await asyncio.gather(
        enrich_from_egrul(party, session),
        enrich_from_fssp(party, session),
        enrich_from_kad(party, session),
    )
    return {"egrul": egrul, "fssp": fssp, "kad": kad}
