"""Адаптер ЕФРСБ — Federal Register of Bankrupt Events."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from src.config import get_settings

if TYPE_CHECKING:
    pass


class SourceAccessError(RuntimeError):
    """The source returned an access or availability error."""


class SourceParseError(RuntimeError):
    """The source responded but its public markup no longer matches the parser."""


LEGACY_EFRSB_HOSTS = frozenset({"old.bankrot.fedresurs.ru", "test.fedresurs.ru"})


def _is_legacy_efrsb_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower() in LEGACY_EFRSB_HOSTS or parsed.path.lower().endswith(
        "/tradelist.aspx"
    )


def _configured_origin(url: str) -> str:
    """Return the configured source origin without a route suffix.

    Operators may set ``EFRSB_PUBLIC_URL`` either to the legacy host or to the
    complete ``TradeList.aspx`` route.  URL-joining card links against the
    latter as a directory produces ``TradeList.aspx/TradeCard.aspx``.
    """
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url.rstrip("/")


def public_search_url() -> str:
    base_url = get_settings().efrsb_public_url.rstrip("/")
    if _is_legacy_efrsb_url(base_url):
        return base_url if base_url.lower().endswith("/tradelist.aspx") else f"{base_url}/TradeList.aspx"
    return f"{base_url}/publications/public/offer"


# ── Паттерны ─────────────────────────────────────────────────────────────────


INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")
DATE_RE = re.compile(
    r"\b(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    r"(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?\b"
)


def parse_price(text: str) -> Decimal | None:
    """Parse a Russian money string without silently inventing a price."""
    if not text:
        return None
    match = re.search(r"\d(?:[\d\s\u00a0.,]*\d)?", text)
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace("\u00a0", "")
    if "," in raw:
        integer, fraction = raw.rsplit(",", 1)
        clean = integer.replace(".", "") + "." + fraction
    elif raw.count(".") == 1 and len(raw.rsplit(".", 1)[1]) <= 2:
        clean = raw
    else:
        clean = raw.replace(".", "")
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


def extract_inn(text: str) -> list[str]:
    """Извлекает уникальные ИНН в порядке появления в тексте."""
    result: list[str] = []
    for inn in INN_RE.findall(text):
        if not _valid_inn(inn):
            continue
        if inn not in result:
            result.append(inn)
    return result


def _valid_inn(inn: str) -> bool:
    """Validate Russian INN check digits for legal entities and individuals."""
    digits = [int(value) for value in inn]
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return (
            sum(digit * weight for digit, weight in zip(digits[:9], weights, strict=True)) % 11 % 10
            == digits[9]
        )
    if len(digits) == 12:
        first_weights = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0)
        second_weights = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        first = (
            sum(digit * weight for digit, weight in zip(digits[:11], first_weights, strict=True))
            % 11
            % 10
        )
        second = (
            sum(digit * weight for digit, weight in zip(digits[:11], second_weights, strict=True))
            % 11
            % 10
        )
        return first == digits[10] and second == digits[11]
    return False


def is_valid_inn(inn: str) -> bool:
    """Public validation helper shared by manual/API debtor assignment."""
    return _valid_inn(inn)


def extract_ogrn(text: str) -> list[str]:
    return list(dict.fromkeys(OGRN_RE.findall(text)))


def extract_debtor_inn(
    description: str | None,
    title: str | None,
    exclude_inns: set[str] | None = None,
) -> str | None:
    """Извлекает ИНН дебитора с приоритетом контекста его роли."""
    if not description and not title:
        return None

    combined = f"{title or ''} {description or ''}"
    lowered = combined.lower()
    excluded = exclude_inns or set()
    candidates = [inn for inn in extract_inn(combined) if inn not in excluded]
    if not candidates:
        return None

    explicit_role_re = re.compile(
        r"(?:право\s+требования\s+к|дебитор(?:ская|у|а)?)\D{0,100}(\d{10}|\d{12})",
        re.IGNORECASE,
    )
    for match in explicit_role_re.finditer(combined):
        if match.group(1) in candidates:
            return match.group(1)

    role_re = re.compile(
        r"(?:должник(?:а|у)?|задолженность)\D{0,100}(\d{10}|\d{12})",
        re.IGNORECASE,
    )
    for match in role_re.finditer(combined):
        context = combined[max(0, match.start() - 35) : match.end() + 35].lower()
        if "банкрот" in context:
            continue
        if match.group(1) in candidates:
            return match.group(1)

    for match in re.finditer(r"ИНН\s*[:№-]?\s*(\d{10}|\d{12})", combined, re.IGNORECASE):
        if match.group(1) in candidates:
            if "банкрот" not in lowered or re.search(
                r"право\s+требования\s+к|дебитор(?:ская|у|а)?\D{0,100}\d",
                combined,
                re.IGNORECASE,
            ):
                return match.group(1)

    # Неподписанный ИНН в карточке часто относится к банкроту/продавцу.
    # Разрешаем fallback только в явном контексте дебиторской задолженности и
    # блокируем карточки, где найден только банкрот/продавец.
    if "банкрот" in lowered and not re.search(
        r"право\s+требования\s+к|дебитор(?:ская|у|а)?\D{0,100}\d",
        combined,
        re.IGNORECASE,
    ):
        return None
    if re.search(r"дебитор|задолжен|право\s+требования", combined, re.IGNORECASE):
        return candidates[0]
    if excluded:
        return candidates[0]
    return None


def _source_timezone(value: str) -> timezone:
    """Return the source timezone.

    EFRSB is a Russian public register and its date cells usually omit the
    timezone marker.  Treating an unqualified value as UTC shifts auction
    deadlines by three hours in Moscow deployments, so Moscow is the safe
    default; an explicit marker still wins.
    """
    if re.search(r"\b(?:мск|московск)", value, re.IGNORECASE):
        return timezone(timedelta(hours=3))
    return timezone(timedelta(hours=3))


def _parse_datetime_match(match: re.Match[str], source_timezone: timezone) -> datetime | None:
    date_text = match.group("date").replace("/", ".").replace("-", ".")
    time_text = match.group("time") or "00:00"
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(f"{date_text} {time_text}", fmt).replace(
                tzinfo=source_timezone
            ).astimezone(UTC)
        except ValueError:
            continue
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%d.%m.%y %H:%M").replace(
            tzinfo=source_timezone
        ).astimezone(UTC)
    except ValueError:
        return None


def _parse_datetimes(value: str) -> list[datetime]:
    """Разбирает все даты в одной ячейке, включая диапазон интервала."""
    values: list[datetime] = []
    source_timezone = _source_timezone(value)
    for match in DATE_RE.finditer(value):
        parsed = _parse_datetime_match(match, source_timezone)
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_datetime(value: str) -> datetime | None:
    """Разбирает дату/время в таблицах PriceReduction."""
    match = DATE_RE.search(value)
    if not match:
        return None
    return _parse_datetime_match(match, _source_timezone(value))


def parse_price_intervals(
    html: str | None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Разбирает шаги публичного предложения из HTML-таблицы."""
    if not html:
        return []
    tree = HTMLParser(html)
    rows = tree.css("tr") or tree.css(".price-interval, .reduction-row, [class*='interval']")
    parsed: list[dict[str, object]] = []
    for row in rows:
        cells = row.css("th, td")
        values = [cell.text().strip() for cell in cells] if cells else [row.text().strip()]
        money_values: list[Decimal] = []
        for value in values:
            if not re.search(r"(?:руб|₽|р\.?\b|price|цена|стоим)", value, re.IGNORECASE):
                continue
            price = parse_price(value)
            if price is not None:
                money_values.append(price)
        if not money_values:
            non_date_values = [value for value in values if not _parse_datetimes(value)]
            for value in reversed(non_date_values):
                if re.fullmatch(r"\s*\d{1,3}\s*", value):
                    continue
                price = parse_price(value)
                if price is not None:
                    money_values.append(price)
                    break
        if not money_values:
            continue
        dates = [parsed for item in values for parsed in _parse_datetimes(item)]
        parsed.append(
            {
                "seq": len(parsed) + 1,
                "price": money_values[-1],
                "starts_at": dates[0] if dates else None,
                "ends_at": dates[1] if len(dates) > 1 else None,
            }
        )
    if not parsed:
        return []
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    current_index: int | None = None
    for index, interval in enumerate(parsed):
        starts_at = interval["starts_at"]
        ends_at = interval["ends_at"]
        if isinstance(starts_at, datetime) and starts_at > reference:
            continue
        if isinstance(ends_at, datetime) and ends_at <= reference:
            continue
        current_index = index
        break
    for index, interval in enumerate(parsed):
        interval["is_current"] = index == current_index
    return parsed


# ── Парсинг страницы лота ────────────────────────────────────────────────────


def parse_lot_card(html: str, url: str) -> dict:
    """Парсит карточку лота с bankrupt-portal.ru."""
    tree = HTMLParser(html)

    title = ""
    title_el = tree.css_first("h1, h2, .page-title, .trade-title")
    if title_el:
        title = title_el.text()

    body_el = tree.body
    body_text = body_el.text(separator=" ", strip=True) if body_el else ""

    # Описание — основной текст. Legacy TradeCard stores the subject in a
    # key/value table rather than a dedicated description class.
    description = ""
    desc_el = tree.css_first('[class*="description"], [class*="content"], .notice-body')
    if desc_el:
        description = desc_el.text()

    # Таблица свойств
    props: dict[str, str] = {}
    rows = tree.css("table.props tr, .props-row, tr")
    for row in rows:
        cells = row.css("th, td, .cell")
        if len(cells) >= 2:
            key = cells[0].text().strip().rstrip(":")
            val = " ".join(c.text().strip() for c in cells[1:])
            if key and key not in props:
                props[key] = val

    if not description:
        description = next(
            (
                value
                for key, value in props.items()
                if any(marker in key.lower() for marker in ("предмет торгов", "описание"))
            ),
            "",
        )

    reduction_el = tree.css_first(
        ".price-reduction, .priceReduction, [class*='price-reduction'], [class*='reduction']"
    )
    if reduction_el is None:
        for candidate in tree.css("table"):
            candidate_text = candidate.text().lower()
            if "цена на интервале" in candidate_text or "величина снижения" in candidate_text:
                reduction_el = candidate
                break
    price_reduction_html = ""
    if reduction_el:
        price_reduction_html = getattr(reduction_el, "html", "") or reduction_el.text()
    lot_match = re.search(
        r"(?:лот|lot)\s*№?\s*(\d+)", f"{title} {description} {body_text}", re.IGNORECASE
    )
    documents: list[dict[str, str]] = []
    etp_url: str | None = None
    etp_host: str | None = None
    etp_trade_id: str | None = None
    fallback_etp: tuple[str, str] | None = None
    for anchor in tree.css("a[href]"):
        href = anchor.attrs.get("href") or ""
        absolute_url = urljoin(url, href)
        parsed_url = urlparse(absolute_url)
        host = (parsed_url.hostname or "").lower()
        anchor_title = anchor.text().strip()
        if host in {"elektortorgi.ru", "utp.sberbank-ast.ru"} or host.endswith(
            (".elektortorgi.ru", ".sberbank-ast.ru")
        ):
            trade_match = re.search(r"/(?:trade|lot)/([^/?#]+)", parsed_url.path, re.IGNORECASE)
            query = parse_qs(parsed_url.query)
            query_trade_id = next(
                (
                    values[0]
                    for key in ("tid", "tradeid", "trade_id", "trade")
                    for values in [query.get(key, [])]
                    if values and values[0]
                ),
                None,
            )
            trade_id = trade_match.group(1) if trade_match else query_trade_id
            is_document = parsed_url.path.lower().endswith((".pdf", ".doc", ".docx"))
            if trade_id and not is_document and etp_url is None:
                etp_url, etp_host, etp_trade_id = absolute_url, host, trade_id
            elif fallback_etp is None and not is_document:
                fallback_etp = (absolute_url, host)
        if (
            parsed_url.scheme in {"http", "https"}
            and (parsed_url.path.lower().endswith((".pdf", ".doc", ".docx")) or
                 any(marker in anchor_title.lower() for marker in ("договор", "положен", "акт", "исполн")))
        ):
            documents.append(
                {"url": absolute_url, "title": anchor_title or parsed_url.path.rsplit("/", 1)[-1]}
            )

    if etp_url is None and fallback_etp is not None:
        etp_url, etp_host = fallback_etp

    if not (title.strip() or description.strip() or props or price_reduction_html or lot_match or documents):
        raise SourceParseError("lot card markers were not found")

    start_price = next(
        (
            parse_price(value)
            for key, value in props.items()
            if "начальн" in key.lower() and parse_price(value) is not None
        ),
        None,
    )
    current_price = next(
        (
            parse_price(value)
            for key, value in props.items()
            if ("текущ" in key.lower() or "итогов" in key.lower())
            and parse_price(value) is not None
        ),
        None,
    )
    return {
        "title": title,
        "description": description,
        "description_html": getattr(desc_el, "html", "") if desc_el else "",
        "price_reduction_html": price_reduction_html,
        "price_intervals": parse_price_intervals(price_reduction_html),
        "lot_no": int(lot_match.group(1)) if lot_match else None,
        "start_price": start_price,
        "current_price": current_price,
        "props": props,
        "url": url,
        "documents": documents,
        "etp_url": etp_url,
        "etp_name": etp_host,
        "trade_id_on_etp": etp_trade_id,
    }


async def _fetch_via_cloakbrowser(url: str, timeout: float) -> str:
    """Use an already running CloakBrowser profile after an HTTP challenge."""
    configured = getattr(get_settings(), "cloakbrowser_cdp_url", "")
    if not configured:
        raise SourceAccessError("source access challenge; CloakBrowser is not configured")
    from src.connectors.cloakbrowser import CloakBrowserError, fetch_html_via_cloakbrowser

    source_host = urlparse(get_settings().efrsb_public_url).hostname or ""
    try:
        return await fetch_html_via_cloakbrowser(
            url,
            cdp_url=configured,
            timeout_seconds=int(getattr(get_settings(), "cloakbrowser_timeout_seconds", timeout)),
            wait_seconds=int(getattr(get_settings(), "cloakbrowser_wait_seconds", 8)),
            allowed_hosts={source_host},
        )
    except CloakBrowserError as exc:
        raise SourceAccessError(f"CloakBrowser fallback failed: {exc}") from exc


async def fetch_page(url: str, timeout: float = 30.0) -> str:
    """Загружает публичную страницу с ограниченными редиректами."""
    await asyncio.sleep(0.5)  # rate limit
    source_url = get_settings().efrsb_public_url
    current = url
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        proxy=get_settings().source_proxy,
    ) as client:
        for redirect_count in range(4):
            if urlparse(current).netloc != urlparse(source_url).netloc:
                raise SourceAccessError("source redirect leaves configured host")
            try:
                resp = await client.get(current, headers={"User-Agent": "AR-Radar/1.0"})
            except httpx.RequestError:
                return await _fetch_via_cloakbrowser(current, timeout)
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                if not location or redirect_count == 3:
                    raise SourceAccessError("source redirect limit exceeded")
                current = urljoin(current, location)
                continue
            if resp.status_code in (401, 403, 429):
                return await _fetch_via_cloakbrowser(current, timeout)
            if resp.status_code >= 500:
                return await _fetch_via_cloakbrowser(current, timeout)
            if resp.status_code == 404:
                raise SourceAccessError(f"source endpoint status=404: {current}")
            resp.raise_for_status()
            if any(marker in resp.text[:5000].lower() for marker in _challenge_markers()):
                return await _fetch_via_cloakbrowser(current, timeout)
            return resp.text
    raise SourceAccessError("source redirect limit exceeded")


def _legacy_trade_anchor(anchor) -> bool:
    href = (anchor.attrs.get("href") or "").lower()
    if "tradecard.aspx" not in href:
        return False
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    return bool(query.get("id") or query.get("tradeid") or query.get("trade_id"))


def _legacy_trade_row(anchor):
    parent = anchor.parent
    for _ in range(6):
        if parent is None:
            return anchor
        if len(parent.css("td, th")) >= 2:
            return parent
        parent = parent.parent
    return anchor


def parse_legacy_public_offers(
    html: str,
    base_url: str,
    *,
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """Parse the public trading table from legacy ``TradeList.aspx``.

    The legacy portal renders an HTML table and links each trade to
    ``TradeCard.aspx?ID=<guid>``.  The list does not expose a lot price, so
    price and debtor details are completed from the card in the ingest worker.
    Rows are filtered locally as a guard in case the server ignores the
    public-offer form value.
    """
    tree = HTMLParser(html)
    hosts = {(urlparse(base_url).hostname or "").lower()}
    items: list[dict] = []
    seen_urls: set[str] = set()
    for anchor in tree.css("a[href]"):
        if not _legacy_trade_anchor(anchor):
            continue
        href = anchor.attrs.get("href") or ""
        item_url = urljoin(f"{_configured_origin(base_url)}/", href)
        parsed_url = urlparse(item_url)
        if parsed_url.scheme not in {"http", "https"} or (
            parsed_url.hostname or ""
        ).lower() not in hosts:
            continue
        if item_url in seen_urls:
            continue
        row = _legacy_trade_row(anchor)
        cells = row.css("td, th")
        cell_text = [" ".join(cell.text().split()) for cell in cells]
        row_text = " ".join(value for value in cell_text if value).strip()
        lowered = row_text.lower()
        if "публичное предложение" not in lowered:
            continue
        seen_urls.add(item_url)

        trade_type = next(
            (
                value
                for value in (
                    "Закрытое публичное предложение",
                    "Публичное предложение",
                )
                if value.lower() in lowered
            ),
            "Публичное предложение",
        )
        status = next(
            (
                value
                for value in (
                    "Открыт прием заявок",
                    "Прием заявок завершен",
                    "Идут торги",
                    "Завершенные",
                    "Аннулированные",
                    "Торги отменены",
                    "Торги не состоялись",
                    "Торги приостановлены",
                    "Объявлены торги",
                )
                if value.lower() in lowered
            ),
            None,
        )
        dates = DATE_RE.findall(row_text)
        date_text = " ".join(
            " ".join(part for part in match if part).strip() for match in dates
        )
        trade_number = ""
        query = {
            key.lower(): values
            for key, values in parse_qs(parsed_url.query).items()
        }
        for key in ("id", "tradeid", "trade_id"):
            if query.get(key):
                trade_number = query[key][0]
                break
        if anchor.text().strip():
            trade_number = anchor.text().strip()

        items.append(
            {
                "title": row_text or f"Торги {trade_number}".strip(),
                "url": item_url,
                "price_text": "",
                "date_text": date_text,
                "source_page": page,
                "trade_number": trade_number,
                "trade_type_label": trade_type,
                "trade_status_label": status,
                "row_text": row_text,
            }
        )
        if len(items) >= max(1, per_page):
            break
    if not items:
        lowered_response = html.lower()
        if any(marker in lowered_response for marker in ("ничего не найдено", "результатов нет")):
            return []
        raise SourceParseError(
            "legacy EFRSB trade rows were not found; source markup may have changed"
        )
    return items


async def search_public_offers(
    page: int = 1,
    per_page: int = 50,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Ищет лоты публичного предложения через HTML или CloakBrowser fallback."""
    legacy_source = _is_legacy_efrsb_url(get_settings().efrsb_public_url)
    params: dict[str, str | int] = (
        {
            "page": page,
            "limit": per_page,
            "type": "public_offer",
        }
        if not legacy_source
        else {
            # The old ASP.NET page may ignore query parameters, therefore the
            # parser also filters the rendered trade type locally.
            "page": page,
            "PageSize": per_page,
            "TradeType": "PublicOffer",
        }
    )

    source_url = public_search_url()
    current = source_url
    response_text: str | None = None
    access_error: SourceAccessError | None = None
    own_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=False,
        proxy=get_settings().source_proxy,
    )
    try:
        for redirect_count in range(4):
            if urlparse(current).netloc != urlparse(get_settings().efrsb_public_url).netloc:
                raise SourceAccessError("source redirect leaves configured host")
            try:
                resp = await active_client.get(
                    current,
                    params=params if redirect_count == 0 else None,
                )
            except httpx.RequestError as exc:
                access_error = SourceAccessError(
                    f"source request failed: {type(exc).__name__}"
                )
                break
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                if not location or redirect_count == 3:
                    raise SourceAccessError("source redirect limit exceeded")
                current = urljoin(current, location)
                continue
            if resp.status_code in (401, 403, 429):
                access_error = SourceAccessError(f"source access status={resp.status_code}")
                break
            if resp.status_code >= 500:
                access_error = SourceAccessError(f"source server status={resp.status_code}")
                break
            if resp.status_code == 404:
                raise SourceAccessError(f"source endpoint status=404: {current}")
            resp.raise_for_status()
            response_text = resp.text
            lowered = response_text[:5000].lower()
            if any(marker in lowered for marker in _challenge_markers()):
                access_error = SourceAccessError("source access challenge status=200")
                response_text = None
                break
            break
        else:
            raise SourceAccessError("source redirect limit exceeded")
    finally:
        if own_client:
            await active_client.aclose()

    if response_text is None:
        if access_error is None:
            raise SourceAccessError("source returned no response")
        try:
            fallback_url = str(httpx.URL(current).copy_merge_params(params))
            response_text = await _fetch_via_cloakbrowser(
                fallback_url,
                timeout=float(getattr(get_settings(), "cloakbrowser_timeout_seconds", 30)),
            )
        except SourceAccessError as fallback_error:
            raise access_error from fallback_error

    if legacy_source:
        return parse_legacy_public_offers(
            response_text,
            get_settings().efrsb_public_url.rstrip("/"),
            page=page,
            per_page=per_page,
        )

    tree = HTMLParser(response_text)
    items: list[dict] = []

    rows = tree.css(".lot-row, .publication-item, .offer-item, [data-lot-id], [data-publication-id]")
    if not rows:
        # A markup change often preserves the lot link while removing the
        # wrapper class.  Recover the nearest structural parent so a selector
        # rename does not silently turn a successful import into an empty run.
        anchors = tree.css("a[href*='/lot/'], a[href*='/publication/'], a[href*='/offer/']")
        recovered = []
        recovered_ids: set[int] = set()
        for anchor in anchors:
            parent = anchor.parent
            for _ in range(4):
                if parent is None:
                    break
                if parent.css("a[href]"):
                    marker = id(parent)
                    if marker not in recovered_ids:
                        recovered.append(parent)
                        recovered_ids.add(marker)
                    break
                parent = parent.parent
        rows = recovered
    if not rows:
        lowered_response = response_text.lower()
        if any(marker in lowered_response for marker in ("ничего не найдено", "по вашему запросу ничего", "результатов нет")):
            return []
        raise SourceParseError("public offer rows were not found; source markup may have changed")

    for row in rows:
        link_el = row.css_first("a[href*='/lot/'], a[href*='/publication/']")
        if not link_el:
            continue

        href = link_el.attrs.get("href") or ""
        title = link_el.text().strip()

        price_el = row.css_first('[class*="price"], [class*="sum"]')
        price_text = price_el.text().strip() if price_el else ""

        date_el = row.css_first("time, .date, [class*='date']")
        date_text = date_el.text().strip() if date_el else ""

        item_url = urljoin(f"{get_settings().efrsb_public_url.rstrip('/')}/", href)
        if urlparse(item_url).netloc != urlparse(get_settings().efrsb_public_url).netloc:
            continue

        items.append({
            "title": title,
            "url": item_url,
            "price_text": price_text,
            "date_text": date_text,
            "source_page": page,
        })

    return items


def _challenge_markers() -> tuple[str, ...]:
    from src.connectors.cloakbrowser import CHALLENGE_MARKERS

    return CHALLENGE_MARKERS


async def iter_all_public_offers(
    max_pages: int = 10,
    per_page: int = 50,
    start_page: int = 1,
) -> AsyncGenerator[dict, None]:
    """Итерирует все страницы публичных предложений."""
    for page in range(max(1, start_page), max_pages + 1):
        items = await search_public_offers(page=page, per_page=per_page)
        if not items:
            break
        for item in items:
            yield item
        await asyncio.sleep(1.0)
