"""Базовый класс для ЭТП-адаптеров."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx

from src.config import get_settings

if TYPE_CHECKING:
    pass


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
    files: list[EtpFile] | None = None


@dataclass
class EtpFile:
    """Файл с ЭТП (документы лота)."""

    title: str
    url: str
    kind: str  # положение, договор, акт, ИЛ, сверка, прочее
    content_type: str = "application/octet-stream"
    size: int | None = None


class EtpAccessError(RuntimeError):
    """ETP returned an access challenge or rate limit."""


class EtpAdapter(ABC):
    """Абстрактный адаптер ЭТП."""

    name: str = "base"
    base_url: str = ""

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        base_host = urlparse(self.base_url).hostname
        self.allowed_hosts = {base_host} if base_host else set()
        self.max_file_bytes = 25 * 1024 * 1024
        self.max_redirects = 3

    async def __aenter__(self) -> EtpAdapter:
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
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

    async def fetch_html(self, url: str) -> tuple[int, str]:
        """Fetch an ETP page, falling back to a configured CloakBrowser profile."""
        if not self._client:
            raise RuntimeError("Adapter not initialized")
        resp = await self._client.get(url, follow_redirects=False)
        if resp.status_code not in (401, 403, 429):
            return resp.status_code, resp.text

        cdp_url = getattr(get_settings(), "cloakbrowser_cdp_url", "")
        if not cdp_url:
            raise EtpAccessError(f"ETP access status={resp.status_code}")
        from src.connectors.cloakbrowser import CloakBrowserError, fetch_html_via_cloakbrowser

        try:
            html = await fetch_html_via_cloakbrowser(
                url,
                cdp_url=cdp_url,
                timeout_seconds=int(getattr(get_settings(), "cloakbrowser_timeout_seconds", 90)),
                wait_seconds=int(getattr(get_settings(), "cloakbrowser_wait_seconds", 8)),
                allowed_hosts=self.allowed_hosts,
            )
        except CloakBrowserError as exc:
            raise EtpAccessError(f"CloakBrowser fallback failed: {exc}") from exc
        return 200, html

    async def download_file(self, file: EtpFile) -> bytes:
        """Download a bounded file from an allowed public host."""
        if not self._client:
            raise RuntimeError("Adapter not initialized")

        url = file.url
        for redirect_count in range(self.max_redirects + 1):
            await self._validate_download_url(url)
            resp = await self._client.get(url, follow_redirects=False)
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                if not location or redirect_count >= self.max_redirects:
                    raise RuntimeError("download redirect limit exceeded")
                url = urljoin(url, location)
                continue
            if resp.status_code in (401, 403, 429):
                cdp_url = getattr(get_settings(), "cloakbrowser_cdp_url", "")
                if not cdp_url:
                    raise EtpAccessError(f"ETP access status={resp.status_code}")
                from src.connectors.cloakbrowser import (
                    CloakBrowserError,
                    fetch_bytes_via_cloakbrowser,
                )

                try:
                    browser_data = await fetch_bytes_via_cloakbrowser(
                        url,
                        cdp_url=cdp_url,
                        timeout_seconds=int(
                            getattr(get_settings(), "cloakbrowser_timeout_seconds", 90)
                        ),
                        allowed_hosts=self.allowed_hosts,
                        max_bytes=self.max_file_bytes,
                    )
                except CloakBrowserError as exc:
                    raise EtpAccessError(f"CloakBrowser fallback failed: {exc}") from exc
                return browser_data
            resp.raise_for_status()
            length = resp.headers.get("content-length")
            if length:
                try:
                    declared_size = int(length)
                except ValueError as exc:
                    raise ValueError("invalid document size") from exc
                if declared_size > self.max_file_bytes:
                    raise ValueError("download exceeds configured size limit")
            streaming_data = bytearray()
            async for chunk in resp.aiter_bytes():
                streaming_data.extend(chunk)
                if len(streaming_data) > self.max_file_bytes:
                    raise ValueError("download exceeds configured size limit")
            return bytes(streaming_data)
        raise RuntimeError("download redirect limit exceeded")

    async def _validate_download_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("document URL must use HTTP(S)")
        if host.lower() not in {h.lower() for h in self.allowed_hosts}:
            raise ValueError("document host is not in the adapter allowlist")

        try:
            addresses = await asyncio.to_thread(
                lambda: {item[4][0] for item in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)}
            )
        except (OSError, ValueError) as exc:
            raise ValueError("document host cannot be resolved") from exc
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ValueError("document host returned an invalid address") from exc
            if not ip.is_global:
                raise ValueError("document host resolves to a non-public address")

    async def rate_limit(self, seconds: float = 1.0) -> None:
        await asyncio.sleep(seconds)
