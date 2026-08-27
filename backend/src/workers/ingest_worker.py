"""Ingest worker — собирает лоты с ЕФРСБ."""
from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal

from selectolax.parser import HTMLParser
from sqlalchemy import select

from src.config import get_settings
from src.models.entities import Lot, Party, Trade
from src.models.enums import (
    DZ_CLASSIFIER_CODES,
    DZ_CLASSIFIER_KEYWORDS,
    ClaimKind,
    PartyRole,
    PersonKind,
    TradeForm,
    TradeKind,
    TradeStatus,
)

logger = logging.getLogger(__name__)
settings = get_settings()


async def is_receivable_lot(
    classifier_codes: list[str],
    classifier_labels: list[str],
    description: str | None,
    title: str | None,
) -> bool:
    """Определяет, является ли лот дебиторской задолженностью."""
    # 1. По коду классификатора
    for code in classifier_codes:
        if code in DZ_CLASSIFIER_CODES:
            return True

    # 2. По названию кода
    for label in classifier_labels:
        for kw in DZ_CLASSIFIER_KEYWORDS:
            if kw in label.lower():
                return True

    # 3. По тексту
    text = f"{title or ''} {description or ''}".lower()
    for kw in DZ_CLASSIFIER_KEYWORDS:
        if kw in text:
            # Исключаем явные негативы
            if "товар" in text and "дебиторск" not in text:
                continue
            return True

    return False


def parse_classifier(html: str) -> tuple[list[str], list[str]]:
    """Парсит блок классификатора имущества."""
    tree = HTMLParser(html)
    codes: list[str] = []
    labels: list[str] = []

    # Обычно идёт таблица
    for tr in tree.css("tr, .classifier-row"):
        cells = tr.css("td, .cell")
        if len(cells) >= 2:
            code = cells[0].text().strip()
            label = " ".join(c.text().strip() for c in cells[1:])
            if code and code[0].isdigit():
                codes.append(code)
                labels.append(label)
            elif label:
                labels.append(label)

    return codes, labels


def parse_price(text: str) -> Decimal | None:
    """Парсит цену из текста вида '12 345,67 руб.'."""
    if not text:
        return None
    clean = re.sub(r"[^\d.,]", "", text)
    if not clean:
        return None
    try:
        return Decimal(clean.replace(",", "."))
    except Exception:
        return None


async def persist_trade_and_lot(card: dict, db) -> tuple[Trade, Lot] | None:
    """Создаёт или обновляет торги + лот в БД."""
    if not card.get("efrsb_url"):
        return None

    # Проверяем, не существует ли уже торг
    stmt = select(Trade).where(Trade.efrsb_url == card["efrsb_url"])
    result = await db.execute(stmt)
    trade = result.scalar_one_or_none()

    if not trade:
        trade = Trade(
            efrsb_url=card["efrsb_url"],
            trade_kind=TradeKind.PUBLIC_OFFER.value,
            trade_form=TradeForm.OPEN.value,
            status=TradeStatus.IN_PROGRESS.value,
        )
        db.add(trade)
        await db.flush()

    # Лот
    lot_no = card.get("lot_no", 1)

    lot_stmt = select(Lot).where(
        Lot.trade_id == trade.id, Lot.lot_no == lot_no
    )
    result = await db.execute(lot_stmt)
    lot = result.scalar_one_or_none()

    if not lot:
        lot = Lot(trade_id=trade.id, lot_no=lot_no)
        db.add(lot)
        await db.flush()

    # Заполняем поля
    lot.title = card.get("title", "")[:500]
    lot.description_text = card.get("description_text")
    lot.description_html = card.get("description_html")
    lot.start_price = card.get("start_price")
    lot.current_price = card.get("current_price") or card.get("start_price")
    lot.current_interval_to = card.get("current_interval_to")
    lot.cutoff_price = card.get("cutoff_price")
    lot.nominal_claimed = card.get("nominal_claimed")
    lot.deposit_amount = card.get("deposit_amount")
    lot.deposit_percent = card.get("deposit_percent")

    lot.classifier_codes = card.get("classifier_codes", [])
    lot.classifier_labels = card.get("classifier_labels", [])
    lot.is_receivable = card.get("is_receivable", False)

    # Банкрот
    bankrupt_inn = card.get("bankrupt_inn")
    if bankrupt_inn:
        bp_stmt = select(Party).where(
            Party.role == PartyRole.BANKRUPT.value, Party.inn == bankrupt_inn
        )
        result = await db.execute(bp_stmt)
        bankrupt = result.scalar_one_or_none()
        if not bankrupt:
            bankrupt = Party(
                role=PartyRole.BANKRUPT.value,
                person_kind=PersonKind.UL.value,
                inn=bankrupt_inn,
                name=card.get("bankrupt_name"),
            )
            db.add(bankrupt)
            await db.flush()
        trade.bankrupt_party_id = bankrupt.id
        trade.case_number = card.get("case_number")

    # Дебитор (claim)
    debtor_inn = card.get("debtor_inn")
    if debtor_inn:
        dp_stmt = select(Party).where(
            Party.role == PartyRole.DEBTOR.value, Party.inn == debtor_inn
        )
        result = await db.execute(dp_stmt)
        debtor = result.scalar_one_or_none()
        if not debtor:
            debtor = Party(
                role=PartyRole.DEBTOR.value,
                person_kind=PersonKind.UL.value,
                inn=debtor_inn,
                name=card.get("debtor_name"),
            )
            db.add(debtor)
            await db.flush()
        # claim
        from src.models.entities import Claim

        claim_stmt = select(Claim).where(Claim.lot_id == lot.id)
        result = await db.execute(claim_stmt)
        claim = result.scalar_one_or_none()
        if not claim:
            claim = Claim(lot_id=lot.id, kind=ClaimKind.TRADE_AR.value)
            db.add(claim)
            await db.flush()
        claim.principal = card.get("nominal_claimed") or card.get("start_price")
        claim.debtor_party_id = debtor.id
        if card.get("has_judgment"):
            claim.has_judgment = True
        if card.get("has_writ"):
            claim.has_writ = True

    return trade, lot


async def run_ingest() -> int:
    """Главный цикл ingest. Возвращает количество обработанных лотов."""
    logger.info("ingest: starting")

    # TODO: Когда появится EFRSB_API_TOKEN — переключить на REST
    # Сейчас заглушка: читаем тестовый поток.
    if not settings.efrsb_api_token:
        logger.warning(
            "ingest: no EFRSB_API_TOKEN, skipping (configure after signing contract)"
        )
        return 0

    # ... настоящий цикл ingest через REST API ...

    return 0


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    while True:
        try:
            count = await run_ingest()
            logger.info("ingest: %d lots processed", count)
        except Exception:
            logger.exception("ingest: failed")
        await asyncio.sleep(settings.ingest_interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(main())
