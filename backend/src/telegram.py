"""Telegram-уведомления через Bot API напрямую (httpx, без aiogram).

Интерактивный бот (/top) живёт в bot/ отдельным сервисом; для алертов из
основного процесса достаточно send_message.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

API_BASE = "https://api.telegram.org"


def fmt_money(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}".replace(",", " ") + " ₽"
    except (ValueError, TypeError):
        return "—"


def fmt_class(cls: str | None) -> str:
    return {"A": "🟢 A", "B": "🟡 B", "C": "🟠 C", "D": "🔴 D"}.get(cls or "", cls or "?")


def fmt_lot_message(lot: dict[str, Any]) -> str:
    """Короткое сообщение по лоту для алерта/дайджеста."""
    claims = lot.get("claims") or [{}]
    debtor = (claims[0] or {}).get("debtor_party") or {}
    stop_factors = lot.get("score_stop_factors") or []

    lines = [
        f"🚨 *Лот класса {fmt_class(lot.get('score_class'))}*",
        "",
        (f"💰 EV: {fmt_money(lot.get('score_ev'))} "
        f"(коридор {fmt_money(lot.get('score_ev_low'))} — {fmt_money(lot.get('score_ev_high'))})"),
        f"🏷 Цена: {fmt_money(lot.get('current_price'))} · Max bid: {fmt_money(lot.get('score_max_bid'))}",
        f"💼 Номинал: {fmt_money(lot.get('nominal_claimed') or lot.get('start_price'))}",
        f"🏢 Дебитор: {debtor.get('name') or '—'} (ИНН {debtor.get('inn') or '—'})",
    ]
    if lot.get("current_interval_to"):
        lines.append(f"⏰ До конца интервала: {str(lot['current_interval_to'])[:16]}")
    if stop_factors:
        lines.append(f"⚠️ Стоп-факторы: {', '.join(str(s) for s in stop_factors)}")

    return "\n".join(lines)


async def send_message(text: str, chat_id: str | None = None) -> bool:
    """Шлёт сообщение во все chat_id (или в один указанный). Markdown."""
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
                        "parse_mode": "Markdown",
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
