"""Telegram-уведомления через Bot API напрямую (httpx, без aiogram).

Интерактивный бот (/top) живёт в bot/ отдельным сервисом; для алертов из
основного процесса достаточно send_message.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from html import escape
from typing import Any
from urllib.parse import urlparse

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

API_BASE = "https://api.telegram.org"


def fmt_money(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    try:
        amount = Decimal(str(value)).quantize(Decimal("1"))
        return f"{amount:,.0f}".replace(",", " ") + " ₽"
    except (ValueError, TypeError, ArithmeticError):
        return "—"


def fmt_class(cls: str | None) -> str:
    return {"A": "🟢 A", "B": "🟡 B", "C": "🟠 C", "D": "🔴 D"}.get(cls or "", cls or "?")


def fmt_lot_message(lot: dict[str, Any]) -> str:
    """Build a safe HTML message for one alert without leaking debtor PII."""
    stop_factors = lot.get("score_stop_factors") or []
    title = str(lot.get("title") or "Лот без названия")

    lines = [
        f"🚨 <b>Лот класса {escape(fmt_class(lot.get('score_class')))}</b>",
        escape(title[:500]),
        "",
        (f"💰 EV: {escape(fmt_money(lot.get('score_ev')))} "
        f"(коридор {fmt_money(lot.get('score_ev_low'))} — {fmt_money(lot.get('score_ev_high'))})"),
        f"🏷 Цена: {escape(fmt_money(lot.get('current_price')))} · Max bid: {escape(fmt_money(lot.get('score_max_bid')))}",
        f"💼 Номинал: {escape(fmt_money(lot.get('nominal_claimed') or lot.get('start_price')))}",
    ]
    if lot.get("current_interval_to"):
        lines.append(f"⏰ До конца интервала: {escape(str(lot['current_interval_to'])[:32])}")
    if stop_factors:
        lines.append(f"⚠️ Стоп-факторы: {escape(', '.join(str(s) for s in stop_factors))}")

    urls: list[str] = []
    for candidate in [lot.get("lot_url"), lot.get("efrsb_url")]:
        if isinstance(candidate, str) and _safe_external_url(candidate):
            urls.append(candidate)
    for ref in lot.get("source_refs") or []:
        candidate = ref.get("source_url") if isinstance(ref, dict) else None
        if isinstance(candidate, str) and _safe_external_url(candidate):
            urls.append(candidate)
    for index, url in enumerate(dict.fromkeys(urls), start=1):
        lines.append(f"🔗 Источник {index}: {escape(url)}")

    return "\n".join(lines)


def _safe_external_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and bool(host)
        and host not in {"localhost", "127.0.0.1", "::1"}
        and not host.startswith("127.")
    )


async def send_message(text: str, chat_id: str | None = None) -> bool:
    """Шлёт HTML-сообщение во все chat_id (или в один указанный)."""
    token = settings.telegram_bot_token
    if not token:
        logger.debug("telegram: no token, skip send")
        return False

    targets = [chat_id] if chat_id else settings.telegram_chat_ids_list
    if not targets:
        logger.debug("telegram: no chat ids, skip send")
        return False

    ok = True
    async with httpx.AsyncClient(timeout=15.0) as client:
        for target in targets:
            try:
                resp = await client.post(
                    f"{API_BASE}/bot{token}/sendMessage",
                    json={
                        "chat_id": target,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("telegram: send to %s failed: %s", target, resp.text[:200])
                    ok = False
            except Exception:
                logger.exception("telegram: send to %s failed", target)
                ok = False
    return ok
