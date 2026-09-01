"""Ingest worker — collects receivables from operational public sources."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime

from selectolax.parser import HTMLParser
from sqlalchemy import delete, select

from src.config import get_settings
from src.connectors.efrsb import (
    SourceAccessError,
    SourceParseError,
    extract_debtor_inn,
    extract_inn,
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
    TradeSourceRef,
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
    is_participable_trade_status,
    normalize_trade_status,
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


def _legacy_trade_status(props: dict[str, str]) -> str | None:
    """Map legacy TradeCard status labels to the internal trade enum."""
    text = " ".join(props.values()).lower()
    mapping = (
        ("открыт прием заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("прием заявок завершен", TradeStatus.IN_PROGRESS.value),
        ("идут торги", TradeStatus.IN_PROGRESS.value),
        ("завершенн", TradeStatus.COMPLETED.value),
        ("аннулирован", TradeStatus.CANCELLED.value),
        ("торги отменен", TradeStatus.CANCELLED.value),
        ("торги не состоял", TradeStatus.DID_NOT_TAKE_PLACE.value),
        ("торги приостанов", TradeStatus.SUSPENDED.value),
        ("объявлены торги", TradeStatus.ANNOUNCED.value),
    )
    for marker, status in mapping:
        if marker in text:
            return status
    return None


def classify_price_schedule(
    intervals: list[dict], now: datetime | None = None
) -> str:
    """Map parsed intervals to a safe operational status."""
    if not intervals:
        return PriceScheduleStatus.NOT_PRESENT.value
    if any(bool(row.get("is_current")) for row in intervals):
        return PriceScheduleStatus.PARSED.value
    if any(not isinstance(row.get("starts_at"), datetime) for row in intervals):
        return PriceScheduleStatus.UNPARSED.value
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    if all(
        isinstance(row.get("starts_at"), datetime)
        and row["starts_at"] > reference
        for row in intervals
    ):
        return PriceScheduleStatus.NOT_STARTED.value
    return PriceScheduleStatus.EXPIRED.value


def _property_inn(props: dict[str, str], *markers: str) -> str | None:
    for key, value in props.items():
        lowered = key.lower()
        if any(marker in lowered for marker in markers):
            inns = extract_inn(value)
            if inns:
                return inns[0]
    return None


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
            normalized_code = code.replace(" ", "")
            looks_like_code = bool(
                normalized_code.isdigit() and len(normalized_code) >= 6
            ) or bool(re.fullmatch(r"\d{2}(?:[.\-]\d{2,})+", normalized_code))
            if not looks_like_code and (
                "%" in row_text
                or "руб" in row_text.lower()
                or normalized_code in {"", "1", "2", "3", "4", "5"}
            ):
                continue
            if code and code[0].isdigit() and looks_like_code:
                codes.append(code)
                labels.append(label)
            elif label:
                labels.append(label)

    return codes, labels


def _lot_score_signature(lot: Lot) -> tuple[object, ...]:
    """Return only fields that can change a score.

    Observation timestamps and source labels are operational metadata. They
    must not invalidate an otherwise identical score on every ingest cycle.
    """
    return (
        lot.start_price,
        lot.current_price,
        lot.cutoff_price,
        lot.nominal_claimed,
        lot.bundle_flag,
        lot.is_receivable,
        lot.price_schedule_status,
        lot.description_text,
    )


def _claim_score_signature(claim: Claim) -> tuple[object, ...]:
    """Return claim fields consumed by the scoring model."""
    return (
        claim.kind,
        claim.principal,
        claim.penalties,
        claim.due_date,
        claim.limitations_deadline,
        claim.has_judgment,
        claim.has_writ,
        claim.enforcement_alive,
        claim.secured,
        claim.assignment_forbidden,
        claim.counterclaim_risk,
        claim.personal_claim,
        claim.debtor_party_id,
        claim.guarantor_party_id,
    )


def _mark_ingest_changed(lot: Lot, changed: bool) -> None:
    """Attach a transient persistence marker for ImportRun counters."""
    setattr(lot, "_ingest_changed", changed)  # noqa: B010


async def persist_trade_and_lot(card: dict, db) -> tuple[Trade, Lot] | None:
    """Создаёт или обновляет торги + лот в БД."""
    source_url = card.get("source_url") or card.get("efrsb_url")
    if not source_url:
        return None

    source_name = str(card.get("source_name") or "efrsb_public")[:50]

    # Проверяем, не существует ли уже торг
    trade = None
    # AsyncSession exposes ``scalar``; the small fake DB used by unit tests
    # intentionally does not, so it keeps exercising the legacy lookup path.
    if hasattr(db, "scalar"):
        source_ref = await db.scalar(
            select(TradeSourceRef).where(
                TradeSourceRef.source == source_name,
                TradeSourceRef.source_url == source_url,
            )
        )
        if source_ref is not None:
            trade = await db.get(Trade, source_ref.trade_id)
    if trade is None:
        stmt = select(Trade).where(Trade.efrsb_url == source_url)
        result = await db.execute(stmt)
        trade = result.scalar_one_or_none()

    if not trade:
        trade_kind = card.get("trade_kind")
        if trade_kind not in {item.value for item in TradeKind}:
            trade_kind = TradeKind.PUBLIC_OFFER.value
        trade = Trade(
            # Keep the old column populated only for the EFRSB compatibility
            # path. CDT and future connectors use TradeSourceRef instead.
            efrsb_url=source_url if source_name == "efrsb_public" else None,
            trade_kind=trade_kind,
            trade_form=TradeForm.OPEN.value,
            status=TradeStatus.IN_PROGRESS.value,
        )
        db.add(trade)
        await db.flush()
    elif source_name != "efrsb_public" and trade.efrsb_url == source_url:
        # Migrate legacy CDT rows lazily as they are observed again; the
        # canonical URL now lives only in TradeSourceRef.
        trade.efrsb_url = None

    trade_status = card.get("trade_status")
    if trade_status in {item.value for item in TradeStatus}:
        trade.status = trade_status

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

    if hasattr(db, "scalar"):
        source_ref = await db.scalar(
            select(TradeSourceRef).where(
                TradeSourceRef.source == source_name,
                TradeSourceRef.source_url == source_url,
            )
        )
        if source_ref is None:
            source_ref = TradeSourceRef(
                trade_id=trade.id,
                source=source_name,
                source_url=source_url,
                external_trade_id=(
                    str(card.get("efrsb_trade_guid") or card.get("trade_id_on_etp"))
                    if (card.get("efrsb_trade_guid") or card.get("trade_id_on_etp"))
                    else None
                ),
                external_lot_id=(str(card["lot_no"]) if card.get("lot_no") is not None else None),
            )
            db.add(source_ref)
        else:
            source_ref.trade_id = trade.id
            source_ref.captured_at = datetime.now(UTC)
            if card.get("efrsb_trade_guid") or card.get("trade_id_on_etp"):
                source_ref.external_trade_id = str(
                    card.get("efrsb_trade_guid") or card.get("trade_id_on_etp")
                )
            if card.get("lot_no") is not None:
                source_ref.external_lot_id = str(card["lot_no"])
        if isinstance(card.get("raw_content"), str) and card["raw_content"]:
            source_ref.content_hash = hashlib.sha256(
                card["raw_content"].encode("utf-8", errors="ignore")
            ).hexdigest()
    snapshot_content_type = str(card.get("snapshot_content_type") or "text/html")[:50]
    raw_content = card.get("raw_content")
    if isinstance(raw_content, str) and raw_content:
        raw_content = raw_content[:1_000_000]
        # Compare against all snapshots of the source URL, not just the latest
        # one, so alternating source HTML cannot grow the table indefinitely.
        existing_snapshot = await db.scalar(
            select(RawSnapshot)
            .where(
                RawSnapshot.source == source_name,
                RawSnapshot.source_url == source_url,
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
            source=source_name,
            source_url=source_url,
            content_type=snapshot_content_type,
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

    lot_is_new = lot is None
    previous_lot_updated_at = lot.updated_at if lot is not None else None
    previous_score_signature = _lot_score_signature(lot) if lot is not None else None
    score_input_changed = lot_is_new

    if lot is None:
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
            # Clear its price, but retain the relevant boundary.  Future
            # schedules are ``not_started``; ended schedules are ``expired``.
            schedule_status = str(
                card.get("price_schedule_status") or PriceScheduleStatus.EXPIRED.value
            )
            upcoming_row = min(
                (row for row in interval_rows if row.get("starts_at") is not None),
                key=lambda row: row["starts_at"],
                default=None,
            )
            boundary_row = upcoming_row if schedule_status == PriceScheduleStatus.NOT_STARTED.value else latest_ended_row
            lot.current_price = None
            lot.current_interval_from = (
                boundary_row.get("starts_at") if boundary_row else None
            )
            lot.current_interval_to = (
                boundary_row.get("ends_at") if boundary_row else None
            )
            lot.price_schedule_status = schedule_status

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
            score_input_changed = True
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
            previous_claim_signature = _claim_score_signature(claim)
            claim_was_new = not claims
            if card.get("nominal_claimed") is not None:
                claim.principal = card["nominal_claimed"]
            claim.debtor_party_id = debtor.id
            if card.get("has_judgment"):
                claim.has_judgment = True
            if card.get("has_writ"):
                claim.has_writ = True
            score_input_changed = score_input_changed or claim_was_new or (
                previous_claim_signature != _claim_score_signature(claim)
            )
            # Relationship changes do not update Lot.updated_at by themselves,
            # so mark its score stale until the score worker recomputes it.
            if score_input_changed:
                lot.updated_at = datetime.now(UTC)

    score_input_changed = score_input_changed or (
        previous_score_signature is not None
        and previous_score_signature != _lot_score_signature(lot)
    )
    if lot_is_new:
        _mark_ingest_changed(lot, True)
    elif score_input_changed:
        _mark_ingest_changed(lot, True)
    else:
        # ``price_observed_at`` is intentionally refreshed on every source
        # poll, but observing the same price does not require a new score.
        # Restore the previous timestamp so dashboards and alerts do not see
        # the whole catalog as stale between scheduled score runs.
        if previous_lot_updated_at is not None:
            lot.updated_at = previous_lot_updated_at
        _mark_ingest_changed(lot, False)

    return trade, lot


async def run_efrsb_ingest() -> int:
    """Run the legacy EFRSB ingest independently from other sources."""
    logger.info("ingest: starting EFRSB")
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
                source_name = str(item.get("source_name") or "efrsb_public")
                source_url = item.get("source_url") or item.get("url")
                title = item.get("title") or ""
                description = item.get("description_text") or item.get("description")
                props: dict[str, str] = {}
                raw_content = item.get("raw_content")
                if not isinstance(raw_content, str):
                    raw_content = None
                detail: dict = {}
                detail_loaded = False
                price_intervals: list[dict] = []
                classifier_codes: list[str] = []
                classifier_labels: list[str] = []
                # Contract REST responses are already normalized and may point
                # to an API host that is intentionally different from the
                # legacy public card host.  Do not turn that URL into an
                # uncontrolled detail request; persist the signed response as
                # the source snapshot instead.
                if item.get("url") and source_name != "efrsb_rest":
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
                start_price = detail.get("start_price") or parse_price(item.get("price_text") or "")
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
                bankrupt_inn = _property_inn(props, "банкрот", "должник")
                organizer_inn = _property_inn(props, "организатор")
                am_inn = _property_inn(props, "управляющ", "арбитражн")
                etp_inn = _property_inn(props, "этп", "электронн")
                excluded_inns = {
                    value
                    for value in (bankrupt_inn, organizer_inn, am_inn, etp_inn)
                    if value
                }
                source_trade_status = item.get("trade_status") or normalize_trade_status(
                    item.get("trade_status_label")
                )
                card = {
                    "source_name": source_name,
                    "source_url": source_url,
                    "efrsb_url": source_url if source_name == "efrsb_public" else None,
                    "snapshot_content_type": item.get("snapshot_content_type") or "text/html",
                    "lot_no": detail.get("lot_no") or item.get("lot_no") or 1,
                    "title": title,
                    "description_text": description,
                    "start_price": start_price,
                    "nominal_claimed": nominal,
                    "raw_content": raw_content,
                    "efrsb_trade_guid": item.get("efrsb_trade_guid"),
                    "trade_id_on_etp": item.get("trade_id_on_etp"),
                    "debtor_inn": extract_debtor_inn(
                        description, title, exclude_inns=excluded_inns
                    ),
                    "bankrupt_inn": bankrupt_inn,
                    "organizer_inn": organizer_inn,
                    "am_inn": am_inn,
                    "etp_inn": etp_inn,
                    "trade_status": source_trade_status,
                }
                if detail_loaded:
                    card.update(
                        {
                            "description_html": detail.get("description_html"),
                            "price_reduction_html": detail.get("price_reduction_html"),
                            "current_price": detail.get("current_price"),
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
                            "trade_status": _legacy_trade_status(props) or source_trade_status,
                            "is_receivable": await is_receivable_lot(
                                classifier_codes, classifier_labels, description, title
                            ),
                            "bundle_flag": any(
                                marker in f"{title} {description or ''}".lower()
                                for marker in ("единый лот", "корзина требований", "в составе лота")
                            ),
                            "price_observed_at": (
                                datetime.now(UTC) if current_interval else None
                            ),
                            "price_source": "efrsb_public",
                            "price_intervals": price_intervals,
                        }
                    )
                    if price_intervals:
                        card.update(
                            {
                                "current_price": current_interval.get("price") if current_interval else None,
                                "current_interval_from": current_interval.get("starts_at") if current_interval else None,
                                "current_interval_to": current_interval.get("ends_at") if current_interval else None,
                                "price_schedule_status": classify_price_schedule(price_intervals),
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
                if not is_participable_trade_status(card.get("trade_status")):
                    run.items_rejected += 1
                    logger.info(
                        "ingest: skipping non-participable public offer %s (status=%s)",
                        source_url,
                        card.get("trade_status") or "unknown",
                    )
                else:
                    saved = await persist_trade_and_lot(card, db)
                    if saved:
                        processed += 1
                        run.items_upserted += 1
                        if getattr(saved[1], "_ingest_changed", True):
                            run.items_changed += 1
                        else:
                            run.items_unchanged += 1
                    else:
                        run.items_rejected += 1
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


async def run_cdt_ingest() -> int:
    """Seed active public-offer receivables from the free public CDT API."""
    from src.connectors.cdt_source import CdtPublicSource
    from src.database import get_db_context

    source_name = "cdt_public"
    processed = 0
    logger.info("ingest: starting CDT public source")
    async with get_db_context() as db:
        run = ImportRun(source=source_name, status="running")
        db.add(run)
        await db.flush()
        await db.commit()
        try:
            async with CdtPublicSource(
                api_url=settings.cdt_api_url,
                detail_concurrency=settings.cdt_detail_concurrency,
                proxy_url=settings.source_proxy,
            ) as source:
                async for card in source.iter_receivables(
                    max_pages=settings.ingest_max_pages,
                    per_page=settings.ingest_page_size,
                    max_items=settings.cdt_ingest_max_items,
                ):
                    run.items_seen += 1
                    saved = await persist_trade_and_lot(card, db)
                    if saved:
                        processed += 1
                        run.items_upserted += 1
                        if getattr(saved[1], "_ingest_changed", True):
                            run.items_changed += 1
                        else:
                            run.items_unchanged += 1
                    else:
                        run.items_rejected += 1
                    run.last_page = processed
                    checkpoint = await db.scalar(
                        select(ImportCheckpoint).where(ImportCheckpoint.source == source_name)
                    )
                    if checkpoint is None:
                        checkpoint = ImportCheckpoint(source=source_name)
                        db.add(checkpoint)
                    checkpoint.cursor = str(card.get("trade_id_on_etp") or processed)
                    checkpoint.updated_at = datetime.now(UTC)
                    await db.commit()
            run.status = "finished"
            run.finished_at = datetime.now(UTC)
            await db.commit()
            logger.info("ingest: processed %d CDT receivables", processed)
        except SourceParseError as exc:
            await db.rollback()
            run.status = "failed"
            run.error_code = "parse_error"
            run.error_message = str(exc)[:500]
            run.finished_at = datetime.now(UTC)
            await db.commit()
            raise
        except SourceAccessError as exc:
            await db.rollback()
            run.status = "paused"
            run.error_code = "source_access"
            run.error_message = str(exc)[:500]
            run.finished_at = datetime.now(UTC)
            await db.commit()
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


async def run_ingest() -> int:
    """Run configured seed sources; one unavailable source cannot block another."""
    runners = {"cdt": run_cdt_ingest, "efrsb": run_efrsb_ingest}
    configured = settings.ingest_sources_list or ["cdt"]
    processed = 0
    completed_sources = 0
    errors: list[Exception] = []
    for source_name in configured:
        runner = runners.get(source_name)
        if runner is None:
            errors.append(ValueError(f"unknown ingest source: {source_name}"))
            logger.error("ingest: unknown configured source %s", source_name)
            continue
        try:
            processed += await runner()
            completed_sources += 1
        except (SourceAccessError, SourceParseError) as exc:
            errors.append(exc)
            logger.warning("ingest: source %s unavailable: %s", source_name, exc)
        except Exception as exc:
            errors.append(exc)
            logger.exception("ingest: source %s failed", source_name)
    if completed_sources:
        return processed
    if errors:
        raise errors[0]
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
