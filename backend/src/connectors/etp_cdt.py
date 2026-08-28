"""Адаптер ЭТП «ЦДТ» (elektortorgi.ru)."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from selectolax.parser import HTMLParser

from src.connectors.etp_base import EtpAccessError, EtpAdapter, EtpFile, EtpLotUpdate

if TYPE_CHECKING:
    pass


PRICE_RE = re.compile(r"(\d[\d\s\u00a0]*[.,]?\d*)")
DATE_RE = re.compile(r"(\d{2}[.\-/]\d{2}[.\-/]\d{4}\s+\d{2}:\d{2})")


def _parse_price(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = PRICE_RE.search(text.replace("\u00a0", " "))
    if not m:
        return None
    clean = m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return Decimal(clean)
    except Exception:
        return None


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y %H:%M")
    except ValueError:
        try:
            return datetime.strptime(m.group(1), "%d-%m-%Y %H:%M")
        except ValueError:
            return None


class CdtAdapter(EtpAdapter):
    """Адаптер для ЭТП ЦДТ (elektortorgi.ru)."""

    name = "cdt"
    base_url = "https://elektortorgi.ru"

    async def fetch_lot(
        self, etp_trade_id: str, lot_no: int
    ) -> EtpLotUpdate | None:
        """Загружает карточку лота на elektortorgi.ru."""
        url = f"{self.base_url}/trade/{etp_trade_id}/lot/{lot_no}"
        if not self._client:
            raise RuntimeError("Adapter not initialized")

        await self.rate_limit(0.5)
        resp = await self._client.get(url)
        if resp.status_code in (401, 403, 429):
            raise EtpAccessError(f"ETP access status={resp.status_code}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        tree = HTMLParser(resp.text)

        # Текущая цена
        current_price = None
        price_el = tree.css_first(".current-price, .price-now, [class*='price']")
        if price_el:
            current_price = _parse_price(price_el.text())

        # Конец интервала
        interval_to = None
        interval_el = tree.css_first(".interval-end, .deadline, [class*='deadline']")
        if interval_el:
            interval_to = _parse_dt(interval_el.text())

        # Задаток
        deposit_amount = None
        deposit_el = tree.css_first(".deposit, [class*='deposit']")
        if deposit_el:
            deposit_amount = _parse_price(deposit_el.text())

        # Статус
        status = None
        status_el = tree.css_first(".status, .state, [class*='status']")
        if status_el:
            status = status_el.text().strip()

        return EtpLotUpdate(
            etp_trade_id=etp_trade_id,
            etp_lot_no=lot_no,
            current_price=current_price,
            current_interval_to=interval_to,
            deposit_amount=deposit_amount,
            status=status,
        )

    async def fetch_files(
        self, etp_trade_id: str, lot_no: int
    ) -> list[EtpFile]:
        """Извлекает ссылки на файлы лота."""
        url = f"{self.base_url}/trade/{etp_trade_id}/lot/{lot_no}"
        if not self._client:
            raise RuntimeError("Adapter not initialized")

        await self.rate_limit(0.5)
        resp = await self._client.get(url)
        if resp.status_code in (401, 403, 429):
            raise EtpAccessError(f"ETP access status={resp.status_code}")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()

        tree = HTMLParser(resp.text)
        files: list[EtpFile] = []

        for a in tree.css("a[href*='/file'], a[href*='/document'], a[href*='.pdf']"):
            href = a.attrs.get("href", "")
            if not href:
                continue
            title = a.text().strip() or "document"
            full_url = href if href.startswith("http") else f"{self.base_url}{href}"

            kind = "прочее"
            title_lc = title.lower()
            for kw, k in [
                ("положение", "положение"),
                ("договор", "договор"),
                ("акт", "акт"),
                ("исполнительн", "ИЛ"),
                ("решение", "решение"),
                ("сверка", "сверка"),
                ("выписка", "выписка"),
            ]:
                if kw in title_lc:
                    kind = k
                    break

            files.append(EtpFile(title=title, url=full_url, kind=kind))

        return files
