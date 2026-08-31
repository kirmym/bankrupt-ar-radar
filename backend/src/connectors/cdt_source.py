"""Public seed connector for the Centre of Distance Trading (CDT).

The public web application at ``torgi.cdtrf.ru`` uses an unauthenticated JSON
API.  Unlike the legacy EFRSB route, it exposes both the active trade list and
the complete public-offer price schedule.  The category directory is noisy, so
the connector deliberately combines server-side text search with a second
receivables check against the detailed lot text.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from src.connectors.efrsb import (
    SourceAccessError,
    SourceParseError,
    extract_debtor_inn,
    parse_price,
)
from src.models.enums import DZ_CLASSIFIER_KEYWORDS, TradeKind, TradeStatus

CDT_SITE_URL = "https://torgi.cdtrf.ru"
CDT_API_URL = "https://webapi.torgi.cdtrf.ru"
CDT_SEARCH_TERMS = ("дебитор", "требован")
CDT_RECEIVABLE_CATEGORY_LABELS = {
    51: "Права требования к физ. лицам",
    52: "Права требования к юр. лицам",
    53: "Смешанная задолженность",
}
MOSCOW_TZ = timezone(timedelta(hours=3))


def _plain_text(value: str | None) -> str:
    if not value:
        return ""
    tree = HTMLParser(value)
    return tree.text(separator=" ", strip=True)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=MOSCOW_TZ).astimezone(UTC)
        except ValueError:
            continue
    return None


def _contains_receivable_text(*values: str | None) -> bool:
    text = " ".join(value or "" for value in values).lower()
    return any(keyword in text for keyword in DZ_CLASSIFIER_KEYWORDS)


def _extract_nominal(text: str) -> Decimal | None:
    """Extract a claim face value without mistaking a lot number for money."""
    patterns = (
        r"(?:в\s+(?:общем\s+)?размере|на\s+сумму)\s*(\d[\d\s\u00a0.,]*)",
        r"номинальн\w*\s+стоимост\w*\s*[:\-]?\s*(\d[\d\s\u00a0.,]*)",
        r"задолженност\w*\s*(?:составляет|в\s+сумме)?\s*[:\-]?\s*(\d[\d\s\u00a0.,]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = parse_price(match.group(1))
            if value is not None:
                return value
    return None


def _trade_status(value: str | None) -> str:
    normalized = (value or "").lower()
    mapping = (
        ("прием заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("приём заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("объявлен", TradeStatus.ANNOUNCED.value),
        ("идут торги", TradeStatus.IN_PROGRESS.value),
        ("подведение итогов", TradeStatus.IN_PROGRESS.value),
        ("заверш", TradeStatus.COMPLETED.value),
        ("не состоял", TradeStatus.DID_NOT_TAKE_PLACE.value),
        ("отмен", TradeStatus.CANCELLED.value),
        ("приостанов", TradeStatus.SUSPENDED.value),
    )
    return next((status for marker, status in mapping if marker in normalized), TradeStatus.ANNOUNCED.value)


def parse_cdt_schedule(
    rows: list[dict[str, Any]] | None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    intervals: list[dict[str, object]] = []
    for row in rows or []:
        price = parse_price(str(row.get("price") or ""))
        if price is None:
            continue
        starts_at = _parse_datetime(row.get("startTime"))
        ends_at = _parse_datetime(row.get("endTime"))
        is_current = bool(
            starts_at is not None
            and starts_at <= reference
            and (ends_at is None or ends_at > reference)
        )
        intervals.append(
            {
                "seq": len(intervals) + 1,
                "price": price,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "is_current": is_current,
            }
        )
    return intervals


def parse_cdt_detail(payload: dict[str, Any], *, now: datetime | None = None) -> dict | None:
    """Convert one public CDT trade into the canonical ingest dictionary."""
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    lot = payload.get("lot")
    trade_id = payload.get("tradeId")
    if not isinstance(lot, dict) or not trade_id:
        raise SourceParseError("CDT detail response has no lot or tradeId")

    title = str(lot.get("name") or payload.get("name") or "").strip()
    description_html = str(lot.get("lotInfo") or "")
    description = _plain_text(description_html)
    if not _contains_receivable_text(title, description):
        return None

    source_url = f"{CDT_SITE_URL}/trades/{trade_id}"
    raw_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    category_ids = [int(value) for value in lot.get("categoryIDs") or [] if str(value).isdigit()]
    category_labels = [
        CDT_RECEIVABLE_CATEGORY_LABELS[value]
        for value in category_ids
        if value in CDT_RECEIVABLE_CATEGORY_LABELS
    ]
    price_intervals = parse_cdt_schedule(lot.get("lotScheduleItems"), now=reference)
    current_interval = next((row for row in price_intervals if row["is_current"]), None)

    bankrupt_inn = str(payload.get("debtJurINN") or payload.get("debtNatINN") or "").strip() or None
    organizer_inn = str(payload.get("orgJurINN") or payload.get("orgNatINN") or "").strip() or None
    am_inn = str(payload.get("arbManINN") or "").strip() or None
    excluded = {value for value in (bankrupt_inn, organizer_inn, am_inn) if value}
    debtor_inn = extract_debtor_inn(description, title, exclude_inns=excluded)

    applications_from = _parse_datetime(payload.get("requestTimeBegin"))
    applications_to = _parse_datetime(payload.get("requestTimeEnd"))
    price_schedule_status = "not_present"
    if price_intervals:
        price_schedule_status = "parsed" if current_interval else (
            "not_started"
            if applications_from is not None and applications_from > reference
            else "expired"
        )

    documents: list[dict[str, str]] = []
    for document in payload.get("docs") or []:
        if not isinstance(document, dict):
            continue
        url = document.get("url") or document.get("fileUrl")
        if url:
            documents.append(
                {
                    "url": str(url),
                    "title": str(document.get("name") or document.get("title") or "document"),
                }
            )

    return {
        "source_name": "cdt_public",
        "snapshot_content_type": "application/json",
        # The current schema uses efrsb_url as the canonical public source URL.
        # Keeping the actual CDT URL here preserves idempotency without a DB migration.
        "efrsb_url": source_url,
        "etp_url": source_url,
        "etp_name": "cdt",
        "trade_id_on_etp": str(trade_id),
        "lot_no": int(lot.get("lotNumber") or 1),
        "title": title,
        "description_text": description,
        "description_html": description_html,
        "raw_content": raw_content,
        "start_price": parse_price(str(lot.get("priceBegin") or "")),
        "nominal_claimed": _extract_nominal(f"{title} {description}"),
        "current_price": current_interval.get("price") if current_interval else None,
        "current_interval_from": current_interval.get("starts_at") if current_interval else None,
        "current_interval_to": current_interval.get("ends_at") if current_interval else None,
        "price_intervals": price_intervals,
        "price_schedule_status": price_schedule_status,
        "price_observed_at": reference,
        "price_source": "cdt_public",
        "classifier_codes": [f"cdt:{value}" for value in category_ids],
        "classifier_labels": category_labels,
        "is_receivable": True,
        "bundle_flag": any(
            marker in f"{title} {description}".lower()
            for marker in ("солидарн", "должникам", "физ.лиц", "перечень", "реестр")
        ),
        "trade_kind": TradeKind.PUBLIC_OFFER.value,
        "trade_status": _trade_status(payload.get("tradeStatusDescription")),
        "applications_from": applications_from,
        "applications_to": applications_to,
        "case_number": payload.get("dealNum"),
        "bankrupt_inn": bankrupt_inn,
        "bankrupt_name": payload.get("debtShortName") or payload.get("debtFullName"),
        "organizer_inn": organizer_inn,
        "organizer_name": payload.get("orgName") or payload.get("orgShortName"),
        "am_inn": am_inn,
        "am_name": " ".join(
            str(payload.get(key) or "").strip()
            for key in ("arbManLastName", "arbManFirstName", "arbManMiddleName")
        ).strip()
        or None,
        "debtor_inn": debtor_inn,
        "documents": documents,
    }


class CdtPublicSource:
    """Bounded async client for the free public CDT JSON endpoints."""

    def __init__(
        self,
        api_url: str = CDT_API_URL,
        *,
        timeout: float = 30.0,
        detail_concurrency: int = 4,
        proxy_url: str | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.detail_concurrency = max(1, min(detail_concurrency, 8))
        self.proxy_url = proxy_url.strip() if proxy_url else None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> CdtPublicSource:
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            timeout=self.timeout,
            follow_redirects=False,
            proxy=self.proxy_url,
            headers={
                "Accept": "application/json",
                "Origin": CDT_SITE_URL,
                "Referer": f"{CDT_SITE_URL}/",
                "User-Agent": "AR-Radar/1.0 (CDT public source)",
            },
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get_json(
        self,
        path: str,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("CDT source is not initialized")
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(path, params=params)
                if response.status_code in {401, 403, 429} or response.status_code >= 500:
                    raise SourceAccessError(f"CDT source access status={response.status_code}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise SourceParseError("CDT response is not a JSON object")
                return payload
            except (httpx.RequestError, SourceAccessError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise SourceAccessError(f"CDT source request failed: {exc}") from exc
            except (ValueError, httpx.HTTPStatusError) as exc:
                raise SourceParseError(f"CDT source returned invalid JSON: {exc}") from exc
        raise SourceAccessError(f"CDT source request failed: {last_error}")

    async def search_page(self, term: str, page: int, per_page: int) -> dict[str, Any]:
        return await self._get_json(
            "/Trade/trades",
            {
                "Declare": "true",
                "RecieveReq": "true",
                "TradeTypeIds": 3,
                "Find": term,
                "PageSize": max(1, min(per_page, 100)),
                "PageNum": max(1, page),
                "Sort": "",
            },
        )

    async def fetch_detail(self, trade_id: int) -> dict[str, Any]:
        return await self._get_json(f"/Trade/public/{trade_id}")

    async def iter_receivables(
        self,
        *,
        max_pages: int = 10,
        per_page: int = 100,
        max_items: int = 250,
    ) -> AsyncGenerator[dict, None]:
        trade_ids: list[int] = []
        seen: set[int] = set()
        for term in CDT_SEARCH_TERMS:
            for page in range(1, max(1, max_pages) + 1):
                payload = await self.search_page(term, page, per_page)
                items = payload.get("items")
                if not isinstance(items, list):
                    raise SourceParseError("CDT search response has no items list")
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    trade_id = item.get("tradeId")
                    if isinstance(trade_id, int) and trade_id not in seen:
                        seen.add(trade_id)
                        trade_ids.append(trade_id)
                total = int(payload.get("totalCount") or 0)
                if not items or page * per_page >= total or len(trade_ids) >= max_items:
                    break
            if len(trade_ids) >= max_items:
                break

        semaphore = asyncio.Semaphore(self.detail_concurrency)

        async def load(trade_id: int) -> dict | None:
            async with semaphore:
                detail = await self.fetch_detail(trade_id)
                return parse_cdt_detail(detail)

        selected = trade_ids[:max_items]
        for offset in range(0, len(selected), self.detail_concurrency * 2):
            batch = selected[offset : offset + self.detail_concurrency * 2]
            cards = await asyncio.gather(*(load(trade_id) for trade_id in batch))
            for card in cards:
                if card is not None:
                    yield card
