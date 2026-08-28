"""Адаптер ЕФРСБ — Federal Register of Bankrupt Events."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
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
    """Извлекает все ИНН из текста. Возвращает уникальный список."""
    inn_candidates = set(INN_RE.findall(text))
    result = []
    for inn in inn_candidates:
        if len(inn) == 10 and inn[0] not in ("0", "1"):
            result.append(inn)
        elif len(inn) == 12:
            result.append(inn)
    return list(dict.fromkeys(result))  # preserve order, remove dupes


def extract_ogrn(text: str) -> list[str]:
    return list(dict.fromkeys(OGRN_RE.findall(text)))


def extract_debtor_inn(description: str | None, title: str | None) -> str | None:
    """Пытается извлечь ИНН дебитора из описания лота.

    Приоритет:
    1. Прямое упоминание «ИНН: 10 цифр»
    2. ОГРН → перебор ИНН
    3. ИНН из текста (с фильтрацией)
    """
    if not description and not title:
        return None

    combined = f"{title or ''} {description or ''}"

    # Паттерн "ИНН: 1234567890"
    inn_label = re.search(r"ИНН[:\s]*(\d{10,12})", combined, re.IGNORECASE)
    if inn_label:
        inn = inn_label.group(1)
        if len(inn) in (10, 12):
            return inn

    # Паттерн "ОГРН: 123456789012345"
    ogrn_match = OGRN_RE.search(combined)
    if ogrn_match:
        ogrn = ogrn_match.group(1)
        if len(ogrn) in (13, 15):
            # Пробуем извлечь ИНН из текста
            inns = extract_inn(combined)
            if inns:
                return inns[0]

    # Просто все ИНН из текста
    inns = extract_inn(combined)
    if inns:
        return inns[0]

    return None


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

    return {
        "title": title,
        "description": description,
        "props": props,
        "url": url,
    }


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
                raise SourceAccessError(f"source access status={resp.status_code}")
            resp.raise_for_status()
            return resp.text
    raise SourceAccessError("source redirect limit exceeded")


async def search_public_offers(
    page: int = 1,
    per_page: int = 50,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Ищет лоты публичного предложения через разрешённую HTML-выдачу."""
    params: dict[str, str | int] = {
        "page": page,
        "limit": per_page,
        "type": "public_offer",
    }

    source_url = public_search_url()
    current = source_url
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
                raise SourceAccessError(f"source access status={resp.status_code}")
            resp.raise_for_status()
            break
        else:
            raise SourceAccessError("source redirect limit exceeded")
    finally:
        if own_client:
            await active_client.aclose()

    tree = HTMLParser(resp.text)
    items: list[dict] = []

    rows = tree.css(".lot-row, .publication-item, .offer-item")
    if not rows and not any(
        marker in resp.text.lower() for marker in ("лот", "торг", "публичн")
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
