"""Telegram-бот — aiogram 3."""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command

from src.config import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def fmt_money(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", " ") + " ₽"


def fmt_class(cls: str | None) -> str:
    return {"A": "🟢 A", "B": "🟡 B", "C": "🟠 C", "D": "🔴 D"}.get(cls or "?", cls or "?")


def fmt_scenario(s: str | None) -> str:
    mapping = {
        "negotiation": "мировое",
        "court": "суд",
        "enforcement": "испол. производство",
        "debtor_bankruptcy": "банкротство дебитора",
        "subsidiary": "субсидиарка",
    }
    return mapping.get(s or "", s or "—")


async def fetch_lots_a_b(base_url: str, limit: int = 10) -> list[dict]:
    """Запрос к API: ленты лотов класса A/B с EV > 0."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base_url}/api/v1/lots",
            params={
                "page": 1,
                "page_size": limit,
                # фильтруем по минимальному EV (10_000_000 = 10 млн)
                "min_ev": 0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])


async def send_alert(bot: Bot, chat_id: str, lot: dict) -> None:
    """Шлёт срочный алерт по одному лоту."""
    debtor = lot.get("claims", [{}])[0].get("debtor_party", {}) if lot.get("claims") else {}

    text = (
        f"🚨 *Новый ликвидный лот!*\n\n"
        f"📌 *Класс:* {fmt_class(lot.get('score_class'))}\n"
        f"💰 *EV:* {fmt_money(lot.get('score_ev'))}\n"
        f"📊 *Коридор EV:* {fmt_money(lot.get('score_ev_low'))} — {fmt_money(lot.get('score_ev_high'))}\n"
        f"🏷 *Цена сейчас:* {fmt_money(lot.get('current_price'))}\n"
        f"🎯 *Max bid:* {fmt_money(lot.get('score_max_bid'))}\n"
        f"🛠 *Сценарий:* {fmt_scenario(lot.get('score_scenario'))}\n"
        f"🏢 *Дебитор:* {debtor.get('name', '—')} "
        f"(ИНН {debtor.get('inn', '—')})\n"
        f"💼 *Номинал:* {fmt_money(lot.get('nominal_claimed'))}\n"
        f"⏰ *До конца интервала:* {lot.get('current_interval_to', '—')[:16]}\n\n"
    )

    stop_factors = lot.get("score_stop_factors", [])
    if stop_factors:
        text += f"⚠️ *Стоп-факторы:* {', '.join(stop_factors)}\n\n"

    text += f"🔗 [Открыть в API](http://localhost:8000/api/v1/lots/{lot.get('id')})"

    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)


async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "👋 *AR Radar Bot*\n\n"
        "Бот присылает алерты по ликвидным лотам публичного предложения.\n\n"
        "Команды:\n"
        "/top — топ лотов по EV\n"
        "/help — справка",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_top(message: types.Message) -> None:
    try:
        lots = await fetch_lots_a_b(settings.api_base_url, limit=10)
    except Exception as e:
        await message.answer(f"❌ Не удалось получить данные: {e}")
        return

    if not lots:
        await message.answer("Нет лотов, удовлетворяющих фильтру.")
        return

    lines = ["📈 *Топ лотов по EV:*\n"]
    for lot in lots[:10]:
        debtor = (
            lot.get("claims", [{}])[0].get("debtor_party", {})
            if lot.get("claims")
            else {}
        )
        lines.append(
            f"{fmt_class(lot.get('score_class'))} "
            f"EV={fmt_money(lot.get('score_ev'))} "
            f"цена={fmt_money(lot.get('current_price'))}\n"
            f"  🏢 {debtor.get('name', '—')[:50]}\n"
            f"  📅 до {lot.get('current_interval_to', '—')[:16]}\n"
        )

    await message.answer("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_help(message: types.Message) -> None:
    await message.answer(
        "ℹ️ *AR Radar*\n\n"
        "Радар мониторит торги по банкротству в РФ и находит ликвидные лоты "
        "с дебиторской задолженностью.\n\n"
        "Команды:\n"
        "/start — приветствие\n"
        "/top — топ лотов\n"
        "/help — эта справка",
        parse_mode=ParseMode.MARKDOWN,
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        return

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_top, Command("top"))
    dp.message.register(cmd_help, Command("help"))

    logger.info("Bot starting…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
