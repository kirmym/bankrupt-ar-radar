"""Базовый класс для ЭТП-адаптеров."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.models.entities import Lot, Trade


@dataclass
class EtpLotUpdate:
    """Что обновляет адаптер с ЭТП."""

    etp_trade_id: str
    etp_lot_no: int
    current_price: Decimal | None = None
    current_interval_from: datetime | None = None
    current_interval_to: datetime | None = None
    cutoff_price: Decimal | None = None
    deposit_amount: Decimal | None = None
    status: str | None = None
    files: list["EtpFile"] | None = None


@dataclass
class EtpFile:
    """Файл с ЭТП (документы лота)."""

    title: str
    url: str
    kind: str  # положение, договор, акт, ИЛ, сверка, прочее
    content_type: str = "application/octet-stream"
    size: int | None = None


class EtpAdapter(ABC):
    """Абстрактный адаптер ЭТП."""

    name: str = "base"
    base_url: str = ""

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "EtpAdapter":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "AR-Radar/1.0 (EtpAdapter)"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    @abstractmethod
    async def fetch_lot(
        self, etp_trade_id: str, lot_no: int
    ) -> EtpLotUpdate | None:
        """Скачивает актуальное состояние лота на ЭТП."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_files(
        self, etp_trade_id: str, lot_no: int
    ) -> list[EtpFile]:
        """Список файлов лота (положения, договоры, акты)."""
        raise NotImplementedError

    async def download_file(self, file: EtpFile) -> bytes:
        """Скачивает содержимое файла."""
        if not self._client:
            raise RuntimeError("Adapter not initialized")
        resp = await self._client.get(file.url)
        resp.raise_for_status()
        return resp.content

    async def rate_limit(self, seconds: float = 1.0) -> None:
        await asyncio.sleep(seconds)
