"""Ingest worker — собирает лоты с ЕФРСБ."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from selectolax.parser import HTMLParser
from sqlalchemy import delete, select

from src.config import get_settings
from src.connectors.efrsb import (
    SourceAccessError,
    SourceParseError,
    extract_debtor_inn,
    fetch_page,
    iter_all_public_offers,
    parse_lot_card,
    parse_price,
)
from src.models.entities import (
    Claim,
    Document,
    ImportCheckpoint,
    ImportRun,
    Lot,
    Party,
    PriceInterval,
    RawSnapshot,
    Trade,
)
from src.models.enums import (
    DZ_CLASSIFIER_CODES,
    DZ_CLASSIFIER_KEYWORDS,
    ClaimKind,
    PartyRole,
    PersonKind,
    PriceScheduleStatus,
    TradeForm,
    TradeKind,
    TradeStatus,
)
from src.workers.document_lock import lock_document

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
            row_text = " ".join(cell.text().strip() for cell in cells)
            if (
                "%" in row_text
                or "руб" in row_text.lower()
                or any(char.isdigit() for char in row_text)
                and any(separator in row_text for separator in (".", ":", "—", "-"))
            ):
                continue
            if code and code[0].isdigit():
                codes.append(code)
                labels.append(label)
            elif label:
                labels.append(label)

    return codes, labels


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

    for field in (
        "efrsb_trade_guid",
        "efrsb_message_guid",
        "trade_id_on_etp",
        "etp_inn",
        "etp_name",
        "etp_url",
        "organizer_inn",
        "organizer_name",
        "am_inn",
        "am_name",
        "applications_from",
        "applications_to",
    ):
        value = card.get(field)
        if value is not None:
            setattr(trade, field, value)

    raw_content = card.get("raw_content")
    if isinstance(raw_content, str) and raw_content:
        raw_content = raw_content[:1_000_000]
        # Compare against all snapshots of the source URL, not just the latest
        # one, so alternating source HTML cannot grow the table indefinitely.
        existing_snapshot = await db.scalar(
            select(RawSnapshot)
            .where(
                RawSnapshot.source == "efrsb_public",
                RawSnapshot.source_url == card["efrsb_url"],
                RawSnapshot.raw_content == raw_content,
            )
            .order_by(RawSnapshot.captured_at.desc())
            .limit(1)
        )
        if existing_snapshot is not None:
            trade.raw_snapshot_id = existing_snapshot.id
            trade.raw_snapshot = existing_snapshot
            raw_content = None
    if isinstance(raw_content, str) and raw_content:
        snapshot = RawSnapshot(
            source="efrsb_public",
            source_url=card["efrsb_url"],
            content_type="text/html",
            raw_content=raw_content[:1_000_000],
        )
        db.add(snapshot)
        await db.flush()
        trade.raw_snapshot_id = snapshot.id
        trade.raw_snapshot = snapshot

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
    text_fields = (
        "title",
        "description_text",
        "description_html",
        "price_reduction_html",
    )
    for field in text_fields:
        value = card.get(field)
        if value is not None:
            setattr(lot, field, value[:500] if field == "title" else value)
    for field in (
        "start_price",
        "cutoff_price",
        "nominal_claimed",
        "deposit_amount",
        "deposit_percent",
    ):
        value = card.get(field)
        if value is not None:
            setattr(lot, field, value)
    # A successfully parsed detail card is authoritative, including explicit
    # ``None`` when no interval is active.  Partial list cards omit the key and
    # therefore keep the last confirmed value.
    if "current_price" in card:
        lot.current_price = card["current_price"]
    elif lot.current_price is None and card.get("start_price") is not None:
        lot.current_price = card["start_price"]
    if "current_interval_from" in card:
        lot.current_interval_from = card["current_interval_from"]
    if "current_interval_to" in card:
        lot.current_interval_to = card["current_interval_to"]
    for field in ("price_schedule_status", "price_observed_at", "price_source"):
        if field in card:
            setattr(lot, field, card[field])
    for field in ("classifier_codes", "classifier_labels"):
        value = card.get(field)
        if value:
            setattr(lot, field, value)
    if "is_receivable" in card and card["is_receivable"] is not None:
        lot.is_receivable = bool(card["is_receivable"])
    if "bundle_flag" in card and card["bundle_flag"] is not None:
        lot.bundle_flag = bool(card["bundle_flag"])

    # Перезаписываем расписание атомарно: источник может изменить шаги публички.
    interval_rows = card.get("price_intervals") or []
    if "price_intervals" in card:
        await db.execute(delete(PriceInterval).where(PriceInterval.lot_id == lot.id))
        active_interval = None
        latest_ended_row = max(
            (row for row in interval_rows if row.get("ends_at") is not None),
            key=lambda row: row["ends_at"],
            default=None,
        )
        for row in interval_rows:
            interval = PriceInterval(
                lot_id=lot.id,
                seq=int(row.get("seq") or 0),
                price=row.get("price"),
                starts_at=row.get("starts_at"),
                ends_at=row.get("ends_at"),
                is_current=bool(row.get("is_current", False)),
            )
            db.add(interval)
            if interval.is_current:
                active_interval = interval
        if active_interval is not None:
            lot.current_price = active_interval.price
            lot.current_interval_from = active_interval.starts_at
            lot.current_interval_to = active_interval.ends_at
            lot.price_schedule_status = PriceScheduleStatus.PARSED.value
        else:
            # The schedule is present but currently outside all intervals.
            # Clear its price, but retain the last known end time.  This makes
            # an expired auction explicitly ineligible for alert delivery.
            lot.current_price = None
            lot.current_interval_from = (
                latest_ended_row.get("starts_at") if latest_ended_row else None
            )
            lot.current_interval_to = (
                latest_ended_row.get("ends_at") if latest_ended_row else None
            )
            lot.price_schedule_status = PriceScheduleStatus.EXPIRED.value

    for file_data in card.get("documents") or []:
        if isinstance(file_data, str):
            file_data = {"url": file_data}
        file_url = file_data.get("url")
        if not file_url:
            continue
        await lock_document(db, lot.id, file_url)
        document = (
            await db.execute(
                select(Document).where(
                    Document.lot_id == lot.id,
                    Document.url == file_url,
                )
            )
        ).scalar_one_or_none()
        if document is None:
            document = Document(lot_id=lot.id, url=file_url)
            db.add(document)
        for field in ("kind", "title"):
            if file_data.get(field) is not None:
                setattr(document, field, str(file_data[field])[:300 if field == "title" else 50])

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
        if card.get("case_number") is not None:
            trade.case_number = card["case_number"]

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
        claim_stmt = select(Claim).where(Claim.lot_id == lot.id).order_by(Claim.id)
        result = await db.execute(claim_stmt)
        claims = result.scalars().all()
        claim: Claim | None = None
        if not claims:
            claim = Claim(lot_id=lot.id, kind=ClaimKind.TRADE_AR.value)
            db.add(claim)
            await db.flush()
        elif len(claims) == 1:
            claim = claims[0]
        else:
            # A list card has one nominal/debtor pair and cannot safely be
            # mapped onto a bundle of detailed claims.
            logger.warning(
                "ingest: skip claim overwrite for lot %d with %d claims",
                lot.id,
                len(claims),
            )

        if claim is not None:
            if card.get("nominal_claimed") is not None:
                claim.principal = card["nominal_claimed"]
            claim.debtor_party_id = debtor.id
            if card.get("has_judgment"):
                claim.has_judgment = True
            if card.get("has_writ"):
                claim.has_writ = True
            # Relationship changes do not update Lot.updated_at by themselves,
            # so mark its score stale until the score worker recomputes it.
            lot.updated_at = datetime.now(UTC)

    return trade, lot


async def run_ingest() -> int:
    """Главный цикл ingest. Возвращает количество обработанных лотов."""
    logger.info("ingest: starting")
    from src.database import get_db_context

    processed = 0
    async with get_db_context() as db:
        previous_run = (
            await db.execute(
                select(ImportRun)
                .where(ImportRun.source == "efrsb_public")
                .order_by(ImportRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        checkpoint = await db.scalar(
            select(ImportCheckpoint).where(ImportCheckpoint.source == "efrsb_public")
        )
        start_page = 1
        if previous_run and previous_run.status in {"running", "paused", "failed"} and checkpoint:
            try:
                start_page = max(1, int(checkpoint.cursor or "1"))
            except ValueError:
                start_page = 1
        run = ImportRun(source="efrsb_public", status="running")
        db.add(run)
        await db.flush()
        await db.commit()
        try:
            async for item in iter_all_public_offers(
                max_pages=settings.ingest_max_pages,
                per_page=settings.ingest_page_size,
                start_page=start_page,
            ):
                run.items_seen += 1
                title = item.get("title") or ""
                description = item.get("description_text") or item.get("description")
                props: dict[str, str] = {}
                raw_content: str | None = None
                detail: dict = {}
                detail_loaded = False
                price_intervals: list[dict] = []
                classifier_codes: list[str] = []
                classifier_labels: list[str] = []
                if item.get("url"):
                    try:
                        raw_content = await fetch_page(item["url"])
                        detail = parse_lot_card(raw_content, item["url"])
                        detail_loaded = True
                        title = detail.get("title") or title
                        description = detail.get("description") or description
                        props = detail.get("props") or {}
                        price_intervals = detail.get("price_intervals") or []
                        classifier_codes, classifier_labels = parse_classifier(raw_content)
                    except SourceAccessError:
                        raise
                    except SourceParseError:
                        # Preserve the parser contract so the outer handler
                        # marks the import as failed instead of checkpointing
                        # a silently partial lot.
                        raise
                    except Exception as exc:
                        logger.warning(
                            "ingest: detail parse failed for %s: %s",
                            item.get("url"),
                            type(exc).__name__,
                        )
                start_price = parse_price(item.get("price_text") or "")
                nominal = next(
                    (
                        parse_price(value)
                        for key, value in props.items()
                        if "номин" in key.lower() and parse_price(value) is not None
                    ),
                    None,
                )
                current_interval = next(
                    (row for row in price_intervals if row.get("is_current")), None
                )
                prop_values = {key.lower(): value for key, value in props.items()}
                cutoff_price = next(
                    (
                        parse_price(value)
                        for key, value in prop_values.items()
                        if "отсеч" in key or "минимальн" in key
                    ),
                    None,
                )
                deposit_value = next(
                    (
                        parse_price(value)
                        for key, value in prop_values.items()
                        if "задат" in key or "обеспеч" in key
                    ),
                    None,
                )
                deposit_text = next(
                    (
                        value
                        for key, value in prop_values.items()
                        if "задат" in key or "обеспеч" in key
                    ),
                    "",
                )
                card = {
                    "efrsb_url": item.get("url"),
                    "lot_no": detail.get("lot_no") or item.get("lot_no") or 1,
                    "title": title,
                    "description_text": description,
                    "start_price": start_price,
                    "nominal_claimed": nominal,
                    "raw_content": raw_content,
                    "debtor_inn": extract_debtor_inn(description, title),
                }
                if detail_loaded:
                    card.update(
                        {
                            "description_html": detail.get("description_html"),
                            "price_reduction_html": detail.get("price_reduction_html"),
                            "cutoff_price": cutoff_price,
                            "deposit_amount": None if "%" in deposit_text else deposit_value,
                            "deposit_percent": deposit_value if "%" in deposit_text else None,
                            "classifier_codes": classifier_codes,
                            "classifier_labels": classifier_labels,
                            "documents": detail.get("documents") or item.get("documents") or [],
                            "etp_url": detail.get("etp_url") or item.get("etp_url"),
                            "trade_id_on_etp": detail.get("trade_id_on_etp") or item.get("trade_id_on_etp"),
                            "etp_name": detail.get("etp_name") or item.get("etp_name"),
                            "etp_inn": detail.get("etp_inn") or item.get("etp_inn"),
                            "is_receivable": await is_receivable_lot(
                                classifier_codes, classifier_labels, description, title
                            ),
                            "bundle_flag": any(
                                marker in f"{title} {description or ''}".lower()
                                for marker in ("единый лот", "корзина требований", "в составе лота")
                            ),
                            "price_observed_at": datetime.now(UTC),
                            "price_source": "efrsb_public",
                        }
                    )
                    if price_intervals:
                        card.update(
                            {
                                "price_intervals": price_intervals,
                                "current_price": current_interval.get("price") if current_interval else None,
                                "current_interval_from": current_interval.get("starts_at") if current_interval else None,
                                "current_interval_to": current_interval.get("ends_at") if current_interval else None,
                                "price_schedule_status": (
                                    PriceScheduleStatus.PARSED.value
                                    if current_interval
                                    else PriceScheduleStatus.EXPIRED.value
                                ),
                            }
                        )
                    elif detail.get("price_reduction_html"):
                        card["price_schedule_status"] = PriceScheduleStatus.UNPARSED.value
                        logger.warning(
                            "ingest: price schedule was present but could not be parsed for %s",
                            item.get("url"),
                        )
                    else:
                        card["price_schedule_status"] = PriceScheduleStatus.NOT_PRESENT.value
                else:
                    # При недоступной карточке сохраняем только поля списка.
                    # Нельзя затирать уже подтвержденные признаки значениями False.
                    card["documents"] = item.get("documents") or []
                    card["etp_url"] = item.get("etp_url")
                    card["trade_id_on_etp"] = item.get("trade_id_on_etp")
                    card["etp_name"] = item.get("etp_name")
                    card["etp_inn"] = item.get("etp_inn")
                saved = await persist_trade_and_lot(card, db)
                if saved:
                    processed += 1
                    run.items_upserted += 1
                page = int(item.get("source_page") or run.last_page or 1)
                run.last_page = max(run.last_page, page)
                checkpoint = await db.scalar(
                    select(ImportCheckpoint).where(
                        ImportCheckpoint.source == "efrsb_public"
                    )
                )
                if checkpoint is None:
                    checkpoint = ImportCheckpoint(source="efrsb_public")
                    db.add(checkpoint)
                checkpoint.cursor = str(run.last_page)
                checkpoint.updated_at = datetime.now(UTC)
                # Durable progress prevents a process restart from hiding work already seen.
                await db.commit()
            run.status = "finished"
            run.finished_at = datetime.now(UTC)
            logger.info("ingest: processed %d public offers", processed)
            await db.commit()
        except SourceParseError as exc:
            await db.rollback()
            run.status = "failed"
            run.error_code = "parse_error"
            run.error_message = str(exc)[:500]
            run.finished_at = datetime.now(UTC)
            await db.commit()
            logger.error("ingest: parser contract failed: %s", exc)
            raise
        except SourceAccessError as exc:
            await db.rollback()
            run.status = "paused"
            run.error_code = "source_access"
            run.error_message = str(exc)[:500]
            run.finished_at = datetime.now(UTC)
            await db.commit()
            logger.warning("ingest: source access paused: %s", exc)
            raise
        except Exception as exc:
            await db.rollback()
            run.status = "failed"
            run.error_code = type(exc).__name__[:50]
            run.error_message = str(exc)[:500]
            run.finished_at = datetime.now(UTC)
            await db.commit()
            raise
    return processed


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
