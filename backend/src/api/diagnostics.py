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
from src.config import Settings, get_settings
from src.connectors.cloakbrowser import (
    CloakBrowserError,
    CloakBrowserHttpError,
    fetch_html_via_cloakbrowser,
)

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_api_access)])

# (имя, URL, считать ли критичным для работы радара)
SOURCES: list[tuple[str, str, bool]] = [
    ("cdt_public", "https://webapi.torgi.cdtrf.ru/Trade/trades", True),
    ("efrsb", "https://bankrot.fedresurs.ru", False),
    ("fedresurs", "https://fedresurs.ru", False),
    ("egrul", "https://egrul.nalog.ru", True),
    ("bo_nalog", "https://bo.nalog.ru", True),
    ("pb_nalog", "https://pb.nalog.ru", False),
    ("kad_arbitr", "https://kad.arbitr.ru", True),
    ("fssp", "https://fssp.gov.ru", True),
    ("telegram_api", "https://api.telegram.org", False),
]


def configured_sources(app_settings: Settings | None = None) -> list[tuple[str, str, bool]]:
    """Build checks from the same endpoints and priorities as the workers."""
    current = app_settings or get_settings()
    cdt_url = (
        f"{current.cdt_api_url.rstrip('/')}/Trade/trades"
        "?Declare=true&RecieveReq=true&TradeTypeIds=3&Find="
        "&PageSize=1&PageNum=1&Sort="
    )
    return [
        ("cdt_public", cdt_url, True),
        ("efrsb", current.efrsb_public_url.rstrip("/"), False),
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
                if resp.status_code in (401, 403, 429)
                else "error"
            ),
            "status_code": resp.status_code,
            "latency_ms": elapsed_ms,
            "critical": critical,
        }
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "source": name,
            "url": url,
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed_ms,
            "state": "transport_timeout",
            "error": type(exc).__name__,
            "critical": critical,
        }
    except httpx.ConnectError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "source": name,
            "url": url,
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed_ms,
            "state": "transport_connect",
            "error": type(exc).__name__,
            "critical": critical,
        }
    except httpx.RequestError as exc:
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


async def _check_browser(name: str, url: str, critical: bool) -> dict:
    """Check the configured browser fallback without hiding direct failures."""
    started = time.monotonic()
    cdp_url = getattr(get_settings(), "cloakbrowser_cdp_url", "")
    if not cdp_url:
        return {
            "source": name,
            "url": url,
            "ok": False,
            "status_code": None,
            "latency_ms": 0,
            "state": "not_configured",
            "critical": critical,
        }
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    try:
        html = await fetch_html_via_cloakbrowser(
            url,
            cdp_url=cdp_url,
            timeout_seconds=int(getattr(get_settings(), "cloakbrowser_timeout_seconds", 30)),
            wait_seconds=0,
            allowed_hosts={host},
        )
        return {
            "source": name,
            "url": url,
            "ok": bool(html),
            "status_code": 200 if html else None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "state": "ok" if html else "empty",
            "critical": critical,
        }
    except CloakBrowserHttpError as exc:
        return {
            "source": name,
            "url": url,
            "ok": False,
            "status_code": exc.status_code,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "state": "http_error",
            "error": type(exc).__name__,
            "critical": critical,
        }
    except CloakBrowserError as exc:
        return {
            "source": name,
            "url": url,
            "ok": False,
            "status_code": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "state": "browser_error",
            "error": type(exc).__name__,
            "critical": critical,
        }


@router.get("/diagnostics/sources")
async def check_sources() -> dict:
    """Пингует все источники данных. Если critical=false у недоступного — не страшно."""
    current_settings = get_settings()
    async with httpx.AsyncClient(
        timeout=10.0,
        headers={
            "Accept": "application/json, text/html;q=0.9",
            "Origin": "https://torgi.cdtrf.ru",
            "Referer": "https://torgi.cdtrf.ru/",
            "User-Agent": "AR-Radar/1.0 (source diagnostics)",
        },
        verify=True,
        proxy=current_settings.source_proxy,
    ) as client:
        results = await asyncio.gather(
            *(
                _check(client, name, url, critical)
                for name, url, critical in configured_sources(current_settings)
            )
        )

    browser_results: list[dict] = []
    if getattr(current_settings, "cloakbrowser_cdp_url", ""):
        from src.connectors.efrsb import public_search_url

        browser_results.append(
            await _check_browser("efrsb_browser", public_search_url(), False)
        )
    browser_ok = {r["source"][:-8]: r["ok"] for r in browser_results if r["source"].endswith("_browser")}
    blocked = [
        r["source"]
        for r in results
        if r["critical"] and not r["ok"] and not browser_ok.get(r["source"], False)
    ]
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "all_critical_ok": not blocked,
        "transport": {
            "source_proxy_configured": bool(current_settings.source_proxy),
            "cloakbrowser_configured": bool(current_settings.cloakbrowser_cdp_url),
        },
        "blocked_critical": blocked,
        "results": results,
        "browser_results": browser_results,
    }
