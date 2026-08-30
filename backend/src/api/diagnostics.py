"""Диагностика: доступность источников данных с текущего хоста.

Нужен для проверки геоблокировки российских реестров с зарубежного
хостинга (Railway). GET /api/v1/diagnostics/sources — точка за точкой.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends

from src.api.security import require_api_access
from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(dependencies=[Depends(require_api_access)])

# (имя, URL, считать ли критичным для работы радара)
SOURCES: list[tuple[str, str, bool]] = [
    ("efrsb", "https://bankrot.fedresurs.ru", True),
    ("fedresurs", "https://fedresurs.ru", False),
    ("egrul", "https://egrul.nalog.ru", True),
    ("bo_nalog", "https://bo.nalog.ru", True),
    ("pb_nalog", "https://pb.nalog.ru", False),
    ("kad_arbitr", "https://kad.arbitr.ru", True),
    ("fssp", "https://fssp.gov.ru", True),
    ("telegram_api", "https://api.telegram.org", False),
]


async def _check(client: httpx.AsyncClient, name: str, url: str, critical: bool) -> dict:
    started = time.monotonic()
    try:
        resp = await client.get(url, follow_redirects=True)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "source": name,
            "url": url,
            "ok": 200 <= resp.status_code < 300,
            "state": (
                "ok"
                if 200 <= resp.status_code < 300
                else "challenge"
                if resp.status_code in (403, 429)
                else "error"
            ),
            "status_code": resp.status_code,
            "latency_ms": elapsed_ms,
            "critical": critical,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "source": name,
            "url": url,
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed_ms,
            "state": "error",
            "error": type(exc).__name__,
            "critical": critical,
        }


@router.get("/diagnostics/sources")
async def check_sources() -> dict:
    """Пингует все источники данных. Если critical=false у недоступного — не страшно."""
    async with httpx.AsyncClient(
        timeout=10.0,
        headers={"User-Agent": "AR-Radar/1.0"},
        verify=True,
    ) as client:
        results = await asyncio.gather(
            *(_check(client, name, url, critical) for name, url, critical in SOURCES)
        )

    blocked = [r["source"] for r in results if r["critical"] and not r["ok"]]
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "all_critical_ok": not blocked,
        "blocked_critical": blocked,
        "results": results,
    }
