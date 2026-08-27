"""Адаптер ЭТП «Сбербанк-АСТ» (банкротный контур)."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from selectolax.parser import HTMLParser

from src.connectors.etp_base import EtpAdapter, EtpFile, EtpLotUpdate
from src.connectors.etp_cdt import _parse_dt, _parse_price

if TYPE_CHECKING:
    pass


class SberbankAdapter(EtpAdapter):
    """Адаптер для ЭТП «Сбербанк-АСТ» (банкротный контур)."""

    name = "sberbank"
    base_url = "https://utp.sberbank-ast.ru"

    async def fetch_lot(
        self, etp_trade_id: str, lot_no: int
    ) -> EtpLotUpdate | None:
        url = f"{self.base_url}/bankrupttrade/TradeCard.aspx?tid={etp_trade_id}&lid={lot_no}"
        if not self._client:
            raise RuntimeError("Adapter not initialized")

        await self.rate_limit(0.5)
        resp = await self._client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        tree = HTMLParser(resp.text)

        current_price = None
        price_el = tree.css_first(".price-block__current, .current-price, [class*='current']")
        if price_el:
            current_price = _parse_price(price_el.text())

        interval_to = None
        deadline_el = tree.css_first(".deadline, [class*='deadline']")
        if deadline_el:
            interval_to = _parse_dt(deadline_el.text())

        return EtpLotUpdate(
            etp_trade_id=etp_trade_id,
            etp_lot_no=lot_no,
            current_price=current_price,
            current_interval_to=interval_to,
        )

    async def fetch_files(
        self, etp_trade_id: str, lot_no: int
    ) -> list[EtpFile]:
        url = f"{self.base_url}/bankrupttrade/TradeCard.aspx?tid={etp_trade_id}&lid={lot_no}"
        if not self._client:
            raise RuntimeError("Adapter not initialized")

        await self.rate_limit(0.5)
        resp = await self._client.get(url)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()

        tree = HTMLParser(resp.text)
        files: list[EtpFile] = []

        for a in tree.css("a[href*='.pdf'], a[href*='/File/'], a[href*='/Document']"):
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
            ]:
                if kw in title_lc:
                    kind = k
                    break

            files.append(EtpFile(title=title, url=full_url, kind=kind))

        return files
