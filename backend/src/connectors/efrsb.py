"""Адаптер ЕФРСБ — Federal Register of Bankrupt Events."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import httpx
from selectolax.parser import HTMLParser

if TYPE_CHECKING:
    pass


EFRSB_BASE = "https://bankrupt-portal.ru"
SEARCH_URL = "https://bankrupt-portal.ru/publications/public/offer"


# ── Паттерны ─────────────────────────────────────────────────────────────────


INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")


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
    """Загружает страницу с задержкой."""
    await asyncio.sleep(0.5)  # rate limit
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "AR-Radar/1.0"})
        resp.raise_for_status()
        return resp.text


async def search_public_offers(
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """Ищет лоты публичного предложения.

    Пока нет договора с ЕФРСБ — парсим bankrupt-portal.ru как демо.
    После получения efrsb_api_token — переключить на REST API.
    """
    params = {
        "page": page,
        "limit": per_page,
        "type": "public_offer",
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(SEARCH_URL, params=params)
        if resp.status_code == 403:
            return []  # нужен real token
        resp.raise_for_status()

    tree = HTMLParser(resp.text)
    items: list[dict] = []

    for row in tree.css(".lot-row, .publication-item, .offer-item"):
        link_el = row.css_first("a[href*='/lot/'], a[href*='/publication/']")
        if not link_el:
            continue

        href = link_el.attrs.get("href", "")
        title = link_el.text().strip()

        price_el = row.css_first('[class*="price"], [class*="sum"]')
        price_text = price_el.text().strip() if price_el else ""

        date_el = row.css_first("time, .date, [class*='date']")
        date_text = date_el.text().strip() if date_el else ""

        items.append({
            "title": title,
            "url": href if href.startswith("http") else f"{EFRSB_BASE}{href}",
            "price_text": price_text,
            "date_text": date_text,
        })

    return items


async def iter_all_public_offers(
    max_pages: int = 10,
    per_page: int = 50,
) -> AsyncGenerator[dict, None]:
    """Итерирует все страницы публичных предложений."""
    for page in range(1, max_pages + 1):
        items = await search_public_offers(page=page, per_page=per_page)
        if not items:
            break
        for item in items:
            yield item
        await asyncio.sleep(1.0)
