"""Enrich-воркер — обогащение дебитора данными из открытых реестров."""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
from selectolax.parser import HTMLParser

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.entities import Party


EGRUL_API = "https://egrul.nic.ru"
FSSP_API = "https://api-ip.fssprus.ru"
KAD_API = "https://bssys.com"


def _d(value: str | None, default: Decimal = Decimal(0)) -> Decimal:
    if not value:
        return default
    try:
        return Decimal(re.sub(r"[^\d.,]", "", value).replace(",", "."))
    except Exception:
        return default


async def enrich_from_egrul(party: Party, session: AsyncSession) -> None:
    """Запрашивает ЕГРЮЛ / ЕГРИП по ИНН через egrul.nic.ru (free API)."""
    if not party.inn:
        return

    inn = party.inn
    url = f"https://egrul.nic.ru/search/?q={inn}&type=ul"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return
        tree = HTMLParser(resp.text)

        # Название
        name_el = tree.css_first("h1, .org-name, [class*='name']")
        if name_el and not party.name:
            party.name = name_el.text().strip()

        # Статус
        if "ликвидирована" in resp.text.lower():
            party.status = "liquidation"
        elif "исключена" in resp.text.lower():
            party.status = "excluded"
        elif "банкротств" in resp.text.lower():
            party.status = "bankruptcy"

        # ОГРН
        ogrn_m = re.search(r"ОГРН[:\s]*(\d{13})", resp.text)
        if ogrn_m and not party.ogrn:
            party.ogrn = ogrn_m.group(1)

        await session.commit()
    except Exception:
        pass


async def enrich_from_fssp(party: Party, session: AsyncSession) -> None:
    """Запрашивает ФССП исполнительные производства по ИНН."""
    if not party.inn:
        return

    try:
        url = f"https://api-ip.fssprus.ru/api/v1.0/search/physical?query={party.inn}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return
            data = resp.json()

        results = data.get("result", [])
        total_sum = Decimal(0)
        uncollectible_count = 0

        for item in results:
            sum_val = _d(item.get("sum", ""))
            total_sum += sum_val
            if item.get("status") == "uncollectible":
                uncollectible_count += 1

        party.fssp_sum = total_sum
        party.fssp_uncollectible = uncollectible_count > 0
        await session.commit()
    except Exception:
        pass


async def enrich_from_kad(party: Party, session: AsyncSession) -> None:
    """Запрашивает КАД арбитражные дела по ИНН."""
    if not party.inn:
        return

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
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return
            data = resp.json()

        total = data.get("total", 0)
        party.kad_as_defendant_count = total
        party.kad_bankruptcy_open = any(
            "банкротств" in str(d).lower()
            for d in data.get("results", [])
        )
        await session.commit()
    except Exception:
        pass


async def enrich_party(party: Party, session: AsyncSession) -> Party:
    """Полное обогащение одного лица — все реестры параллельно."""
    await asyncio.gather(
        enrich_from_egrul(party, session),
        enrich_from_fssp(party, session),
        enrich_from_kad(party, session),
        return_exceptions=True,
    )
    return party
