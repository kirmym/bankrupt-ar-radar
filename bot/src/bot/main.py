"""Telegram-бот — aiogram 3."""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from html import escape
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.config import settings

logger = logging.getLogger(__name__)


class ApiLot(BaseModel):
    """The small, validated API contract used by the interactive bot."""

    model_config = ConfigDict(extra="ignore")

    id: int
    score_class: str | None = None
    score_ev: Decimal | None = None
    score_ev_low: Decimal | None = None
    score_ev_high: Decimal | None = None
    current_price: Decimal | None = None
    score_max_bid: Decimal | None = None
    score_scenario: str | None = None
    nominal_claimed: Decimal | None = None
    current_interval_to: str | None = None
    price_schedule_status: str | None = None
    price_observed_at: str | None = None
    score_updated_at: str | None = None
    score_stop_factors: list[str] = Field(default_factory=list)


class ApiLotList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ApiLot]


def fmt_money(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    try:
        return f"{Decimal(str(value)):,.0f}".replace(",", " ") + " ₽"
    except (ValueError, TypeError, ArithmeticError):
        return "—"


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
        headers = {"X-API-Key": settings.api_auth_token} if settings.api_auth_token else {}
        lots: list[dict] = []
        for score_class in ("A", "B"):
            for trade_status in ("announced", "applications_open", "in_progress"):
                resp = await client.get(
                    f"{base_url.rstrip('/')}/api/v1/lots",
                    params={
                        "page": 1,
                        "page_size": limit,
                        "min_ev": 0,
                        "score_class": score_class,
                        "price_status": "parsed",
                        "trade_status": trade_status,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                payload = ApiLotList.model_validate(resp.json())
                lots.extend(item.model_dump(mode="json") for item in payload.items)
        active = [
            lot
            for lot in lots
            if lot.get("price_schedule_status") == "parsed"
            and lot.get("current_price") is not None
        ]
        unique = {int(lot["id"]): lot for lot in active if lot.get("id") is not None}
        return sorted(
            unique.values(),
            key=lambda lot: Decimal(str(lot.get("score_ev") or 0)),
            reverse=True,
        )[:limit]


def _is_allowed(message: types.Message) -> bool:
    allowed = settings.telegram_allowed_user_ids_list
    if allowed:
        return bool(message.from_user and message.from_user.id in allowed)
    return settings.bot_public


async def send_alert(bot: Bot, chat_id: str, lot: dict) -> None:
    """Шлёт срочный алерт по одному лоту."""
    text = (
        "🚨 <b>Новый ликвидный лот!</b>\n\n"
        f"📌 <b>Класс:</b> {escape(fmt_class(lot.get('score_class')))}\n"
        f"💰 <b>EV:</b> {escape(fmt_money(lot.get('score_ev')))}\n"
        f"📊 <b>Коридор EV:</b> {escape(fmt_money(lot.get('score_ev_low')))} — {escape(fmt_money(lot.get('score_ev_high')))}\n"
        f"🏷 <b>Цена сейчас:</b> {escape(fmt_money(lot.get('current_price')))}\n"
        f"🎯 <b>Max bid:</b> {escape(fmt_money(lot.get('score_max_bid')))}\n"
        f"🛠 <b>Сценарий:</b> {escape(fmt_scenario(lot.get('score_scenario')))}\n"
        f"💼 <b>Номинал:</b> {escape(fmt_money(lot.get('nominal_claimed')))}\n"
        f"⏰ <b>До конца интервала:</b> {escape(str(lot.get('current_interval_to') or '—')[:32])}\n\n"
    )

    stop_factors = lot.get("score_stop_factors", [])
    if stop_factors:
        text += f"⚠️ <b>Стоп-факторы:</b> {escape(', '.join(str(value) for value in stop_factors))}\n\n"

    api_url = f"{settings.api_base_url.rstrip('/')}/api/v1/lots/{lot.get('id')}"
    parsed = urlparse(api_url)
    if parsed.scheme in {"http", "https"} and parsed.hostname not in {"localhost", "backend", "127.0.0.1", "::1"}:
        text += f"🔗 <a href=\"{escape(api_url, quote=True)}\">Открыть карточку</a>"

    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)


async def cmd_start(message: types.Message) -> None:
    if not _is_allowed(message):
        return
    await message.answer(
        "👋 *AR Radar Bot*\n\n"
        "Бот присылает алерты по ликвидным лотам публичного предложения.\n\n"
        "Команды:\n"
        "/top — топ лотов по EV\n"
        "/help — справка",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_top(message: types.Message) -> None:
    if not _is_allowed(message):
        return
    try:
        lots = await fetch_lots_a_b(settings.api_base_url, limit=10)
    except (httpx.HTTPError, ValidationError, ValueError, ArithmeticError) as e:
        await message.answer(f"❌ Не удалось получить данные: {e}")
        return

    if not lots:
        await message.answer("Нет лотов, удовлетворяющих фильтру.")
        return

    lines = ["📈 *Топ лотов по EV:*\n"]
    for lot in lots[:10]:
        lines.append(
            f"{fmt_class(lot.get('score_class'))} "
            f"EV={fmt_money(lot.get('score_ev'))} "
            f"цена={fmt_money(lot.get('current_price'))}\n"
            f"  📅 до {str(lot.get('current_interval_to') or '—')[:16]}\n"
        )

    await message.answer("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_help(message: types.Message) -> None:
    if not _is_allowed(message):
        return
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
