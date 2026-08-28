"""Адаптер ЕФРСБ — Federal Register of Bankrupt Events."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from src.config import get_settings

if TYPE_CHECKING:
    pass


class SourceAccessError(RuntimeError):
    """The source returned an access or availability error."""


class SourceParseError(RuntimeError):
    """The source responded but its public markup no longer matches the parser."""


def public_search_url() -> str:
    return f"{get_settings().efrsb_public_url.rstrip('/')}/publications/public/offer"


# ── Паттерны ─────────────────────────────────────────────────────────────────


INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")
DATE_RE = re.compile(
    r"\b(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    r"(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?\b"
)


def parse_price(text: str) -> Decimal | None:
    """Parse a Russian money string without silently inventing a price."""
    if not text:
        return None
    match = re.search(r"\d(?:[\d\s\u00a0.,]*\d)?", text)
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace("\u00a0", "")
    if "," in raw:
        integer, fraction = raw.rsplit(",", 1)
        clean = integer.replace(".", "") + "." + fraction
    elif raw.count(".") == 1 and len(raw.rsplit(".", 1)[1]) <= 2:
        clean = raw
    else:
        clean = raw.replace(".", "")
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


def extract_inn(text: str) -> list[str]:
    """Извлекает уникальные ИНН в порядке появления в тексте."""
    result: list[str] = []
    for inn in INN_RE.findall(text):
        if len(inn) == 10 and inn[0] in ("0", "1"):
            continue
        if inn not in result:
            result.append(inn)
    return result


def extract_ogrn(text: str) -> list[str]:
    return list(dict.fromkeys(OGRN_RE.findall(text)))


def extract_debtor_inn(
    description: str | None,
    title: str | None,
    exclude_inns: set[str] | None = None,
) -> str | None:
    """Извлекает ИНН дебитора с приоритетом контекста его роли."""
    if not description and not title:
        return None

    combined = f"{title or ''} {description or ''}"
    excluded = exclude_inns or set()
    candidates = [inn for inn in extract_inn(combined) if inn not in excluded]
    if not candidates:
        return None

    role_re = re.compile(
        r"(?:дебитор(?:ская|у|а)?|должник(?:а|у)?|задолженность|"
        r"право\s+требования\s+к)\D{0,100}(\d{10}|\d{12})",
        re.IGNORECASE,
    )
    for match in role_re.finditer(combined):
        if match.group(1) in candidates:
            return match.group(1)

    for match in re.finditer(r"ИНН\s*[:№-]?\s*(\d{10}|\d{12})", combined, re.IGNORECASE):
        if match.group(1) in candidates:
            return match.group(1)

    return candidates[0]


def _parse_datetimes(value: str) -> list[datetime]:
    """Разбирает все даты в одной ячейке, включая диапазон интервала."""
    values: list[datetime] = []
    for match in DATE_RE.finditer(value):
        parsed = _parse_datetime(match.group(0))
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_datetime(value: str) -> datetime | None:
    """Разбирает дату/время в таблицах PriceReduction."""
    match = DATE_RE.search(value)
    if not match:
        return None
    date_text = match.group("date").replace("/", ".").replace("-", ".")
    time_text = match.group("time") or "00:00"
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(f"{date_text} {time_text}", fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%d.%m.%y %H:%M").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_price_intervals(
    html: str | None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Разбирает шаги публичного предложения из HTML-таблицы."""
    if not html:
        return []
    tree = HTMLParser(html)
    rows = tree.css("tr") or tree.css(".price-interval, .reduction-row, [class*='interval']")
    parsed: list[dict[str, object]] = []
    for row in rows:
        cells = row.css("th, td")
        values = [cell.text().strip() for cell in cells] if cells else [row.text().strip()]
        money_values: list[Decimal] = []
        for value in values:
            if not re.search(r"(?:руб|₽|р\.?\b|price|цена|стоим)", value, re.IGNORECASE):
                continue
            price = parse_price(value)
            if price is not None:
                money_values.append(price)
        if not money_values:
            non_date_values = [value for value in values if not _parse_datetimes(value)]
            for value in reversed(non_date_values):
                price = parse_price(value)
                if price is not None:
                    money_values.append(price)
                    break
        if not money_values:
            continue
        dates = [parsed for item in values for parsed in _parse_datetimes(item)]
        parsed.append(
            {
                "seq": len(parsed) + 1,
                "price": money_values[-1],
                "starts_at": dates[0] if dates else None,
                "ends_at": dates[1] if len(dates) > 1 else None,
            }
        )
    if not parsed:
        return []
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    current_index: int | None = None
    for index, interval in enumerate(parsed):
        starts_at = interval["starts_at"]
        ends_at = interval["ends_at"]
        if isinstance(starts_at, datetime) and starts_at > reference:
            continue
        if isinstance(ends_at, datetime) and ends_at <= reference:
            continue
        current_index = index
        break
    if current_index is None:
        current_index = len(parsed) - 1
    for index, interval in enumerate(parsed):
        interval["is_current"] = index == current_index
    return parsed


# ── Парсинг страницы лота ────────────────────────────────────────────────────


def parse_lot_card(html: str, url: str) -> dict:
    """Парсит карточку лота с bankrupt-portal.ru."""
    tree = HTMLParser(html)

    title = ""
    title_el = tree.css_first("h1")
    if title_el:
        title = title_el.text()

    # Описание — основной текст
    description = ""
    desc_el = tree.css_first('[class*="description"], [class*="content"], .notice-body')
    if desc_el:
        description = desc_el.text()

    # Таблица свойств
    props: dict[str, str] = {}
    rows = tree.css("table.props tr, .props-row")
    for row in rows:
        cells = row.css("td, .cell")
        if len(cells) >= 2:
            key = cells[0].text().strip().rstrip(":")
            val = " ".join(c.text().strip() for c in cells[1:])
            props[key] = val

    reduction_el = tree.css_first(
        ".price-reduction, .priceReduction, [class*='price-reduction'], [class*='reduction']"
    )
    price_reduction_html = ""
    if reduction_el:
        price_reduction_html = getattr(reduction_el, "html", "") or reduction_el.text()
    lot_match = re.search(r"(?:лот|lot)\s*№?\s*(\d+)", f"{title} {description}", re.IGNORECASE)

    return {
        "title": title,
        "description": description,
        "description_html": getattr(desc_el, "html", "") if desc_el else "",
        "price_reduction_html": price_reduction_html,
        "price_intervals": parse_price_intervals(price_reduction_html),
        "lot_no": int(lot_match.group(1)) if lot_match else None,
        "props": props,
        "url": url,
    }


async def _fetch_via_cloakbrowser(url: str, timeout: float) -> str:
    """Use an already running CloakBrowser profile after an HTTP challenge."""
    configured = getattr(get_settings(), "cloakbrowser_cdp_url", "")
    if not configured:
        raise SourceAccessError("source access challenge; CloakBrowser is not configured")
    from src.connectors.cloakbrowser import CloakBrowserError, fetch_html_via_cloakbrowser

    source_host = urlparse(get_settings().efrsb_public_url).hostname or ""
    try:
        return await fetch_html_via_cloakbrowser(
            url,
            cdp_url=configured,
            timeout_seconds=int(getattr(get_settings(), "cloakbrowser_timeout_seconds", timeout)),
            wait_seconds=int(getattr(get_settings(), "cloakbrowser_wait_seconds", 8)),
            allowed_hosts={source_host},
        )
    except CloakBrowserError as exc:
        raise SourceAccessError(f"CloakBrowser fallback failed: {exc}") from exc


async def fetch_page(url: str, timeout: float = 30.0) -> str:
    """Загружает публичную страницу с ограниченными редиректами."""
    await asyncio.sleep(0.5)  # rate limit
    source_url = get_settings().efrsb_public_url
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for redirect_count in range(4):
            if urlparse(current).netloc != urlparse(source_url).netloc:
                raise SourceAccessError("source redirect leaves configured host")
            resp = await client.get(current, headers={"User-Agent": "AR-Radar/1.0"})
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                if not location or redirect_count == 3:
                    raise SourceAccessError("source redirect limit exceeded")
                current = urljoin(current, location)
                continue
            if resp.status_code in (401, 403, 429):
                return await _fetch_via_cloakbrowser(current, timeout)
            resp.raise_for_status()
            return resp.text
    raise SourceAccessError("source redirect limit exceeded")


async def search_public_offers(
    page: int = 1,
    per_page: int = 50,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Ищет лоты публичного предложения через HTML или CloakBrowser fallback."""
    params: dict[str, str | int] = {
        "page": page,
        "limit": per_page,
        "type": "public_offer",
    }

    source_url = public_search_url()
    current = source_url
    response_text: str | None = None
    access_error: SourceAccessError | None = None
    own_client = client is None
    active_client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    try:
        for redirect_count in range(4):
            if urlparse(current).netloc != urlparse(get_settings().efrsb_public_url).netloc:
                raise SourceAccessError("source redirect leaves configured host")
            resp = await active_client.get(current, params=params if redirect_count == 0 else None)
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                if not location or redirect_count == 3:
                    raise SourceAccessError("source redirect limit exceeded")
                current = urljoin(current, location)
                continue
            if resp.status_code in (401, 403, 429):
                access_error = SourceAccessError(f"source access status={resp.status_code}")
                break
            resp.raise_for_status()
            response_text = resp.text
            break
        else:
            raise SourceAccessError("source redirect limit exceeded")
    finally:
        if own_client:
            await active_client.aclose()

    if response_text is None:
        if access_error is None:
            raise SourceAccessError("source returned no response")
        try:
            response_text = await _fetch_via_cloakbrowser(
                current,
                timeout=float(getattr(get_settings(), "cloakbrowser_timeout_seconds", 30)),
            )
        except SourceAccessError as fallback_error:
            raise access_error from fallback_error

    tree = HTMLParser(response_text)
    items: list[dict] = []

    rows = tree.css(".lot-row, .publication-item, .offer-item")
    if not rows and not any(
        marker in response_text.lower() for marker in ("лот", "торг", "публичн")
    ):
        raise SourceParseError("public offer rows were not found")

    for row in rows:
        link_el = row.css_first("a[href*='/lot/'], a[href*='/publication/']")
        if not link_el:
            continue

        href = link_el.attrs.get("href") or ""
        title = link_el.text().strip()

        price_el = row.css_first('[class*="price"], [class*="sum"]')
        price_text = price_el.text().strip() if price_el else ""

        date_el = row.css_first("time, .date, [class*='date']")
        date_text = date_el.text().strip() if date_el else ""

        item_url = urljoin(f"{get_settings().efrsb_public_url.rstrip('/')}/", href)
        if urlparse(item_url).netloc != urlparse(get_settings().efrsb_public_url).netloc:
            continue

        items.append({
            "title": title,
            "url": item_url,
            "price_text": price_text,
            "date_text": date_text,
            "source_page": page,
        })

    return items


async def iter_all_public_offers(
    max_pages: int = 10,
    per_page: int = 50,
    start_page: int = 1,
) -> AsyncGenerator[dict, None]:
    """Итерирует все страницы публичных предложений."""
    for page in range(max(1, start_page), max_pages + 1):
        items = await search_public_offers(page=page, per_page=per_page)
        if not items:
            break
        for item in items:
            yield item
        await asyncio.sleep(1.0)
