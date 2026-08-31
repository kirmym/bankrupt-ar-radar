"""Тесты для ИНН-экстрактора."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from src.connectors.efrsb import (
    SourceAccessError,
    SourceParseError,
    extract_debtor_inn,
    extract_inn,
    extract_ogrn,
    parse_legacy_public_offers,
    parse_lot_card,
    parse_price_intervals,
    public_search_url,
    search_public_offers,
)


def test_extract_inn_10_digits():
    text = "ООО Ромашка ИНН 7707083893 зарегистрировано"
    result = extract_inn(text)
    assert "7707083893" in result


def test_extract_inn_12_digits():
    text = "ИП Иванов ИНН 500100732259"
    result = extract_inn(text)
    assert "500100732259" in result


def test_extract_inn_no_inn():
    assert extract_inn("нет инн") == []


def test_extract_inn_filters_invalid():
    text = "ИНН 0123456789"  # 10 знаков, начинается с 0 — невалидный
    result = extract_inn(text)
    assert "0123456789" not in result


def test_extract_inn_validates_checksum_without_region_shortcut():
    assert extract_inn("ИНН 1600000011") == ["1600000011"]
    assert extract_inn("ИНН 7707083894") == []


def test_extract_inn_unique():
    text = "ИНН 7707083893 ИНН 7707083893"
    result = extract_inn(text)
    assert len(result) == 1


def test_extract_ogrn_13():
    text = "ОГРН 1027700132195"
    result = extract_ogrn(text)
    assert "1027700132195" in result


def test_extract_debtor_inn_with_label():
    text = "Право требования к ООО «Ромашка» ИНН 7707083893"
    inn = extract_debtor_inn(text, None)
    assert inn == "7707083893"


def test_extract_debtor_inn_with_title():
    text = "Дебиторская задолженность ООО Ромашка ОГРН 1027700132195"
    title = "Лот №1: 7707083893"
    inn = extract_debtor_inn(text, title)
    assert inn == "7707083893"


def test_extract_debtor_inn_empty():
    assert extract_debtor_inn(None, None) is None


def test_extract_debtor_inn_no_match():
    text = "Просто текст без цифр"
    assert extract_debtor_inn(text, None) is None


def test_public_search_url_uses_legacy_trade_list_for_old_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(efrsb_public_url="https://old.bankrot.fedresurs.ru"),
    )
    assert public_search_url() == "https://old.bankrot.fedresurs.ru/TradeList.aspx"


def test_legacy_trade_list_parser_extracts_public_offer_rows() -> None:
    html = """
    <table id="trade-list">
      <tr><th>Номер торгов</th><th>Дата торгов</th><th>Должник</th><th>Вид торгов</th><th>Статус</th></tr>
      <tr>
        <td><a href="/TradeCard.aspx?ID=91c40da1-20c7-46f5-ad14-ab8b20bce52c">333593</a></td>
        <td>27.04.2026 11:00</td><td>ООО Ромашка</td>
        <td>Публичное предложение</td><td>Объявлены торги</td>
      </tr>
      <tr>
        <td><a href="/TradeCard.aspx?ID=aaaaaaaa-20c7-46f5-ad14-ab8b20bce52c">333594</a></td>
        <td>27.04.2026 11:00</td><td>ООО Завод</td>
        <td>Открытый аукцион</td><td>Объявлены торги</td>
      </tr>
    </table>
    """
    result = parse_legacy_public_offers(
        html,
        "https://old.bankrot.fedresurs.ru",
        page=2,
        per_page=50,
    )
    assert len(result) == 1
    assert result[0]["url"].endswith("TradeCard.aspx?ID=91c40da1-20c7-46f5-ad14-ab8b20bce52c")
    assert result[0]["trade_number"] == "333593"
    assert result[0]["trade_status_label"] == "Объявлены торги"
    assert result[0]["source_page"] == 2


def test_legacy_trade_list_parser_accepts_configured_route_url() -> None:
    html = """
    <table><tr><td>Публичное предложение</td><td>
      <a href="/TradeCard.aspx?ID=abc-123">Лот 1</a>
    </td></tr></table>
    """
    result = parse_legacy_public_offers(
        html,
        "https://old.bankrot.fedresurs.ru/TradeList.aspx",
    )
    assert result[0]["url"] == "https://old.bankrot.fedresurs.ru/TradeCard.aspx?ID=abc-123"


@pytest.mark.asyncio
async def test_legacy_public_offer_search_uses_trade_type_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(efrsb_public_url="https://old.bankrot.fedresurs.ru"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/TradeList.aspx"
        assert request.url.params["TradeType"] == "PublicOffer"
        return httpx.Response(
            200,
            text=(
                '<table><tr><td><a href="/TradeCard.aspx?ID=91c40da1-20c7-46f5-ad14-ab8b20bce52c">'
                "333593</a></td><td>Публичное предложение</td><td>Объявлены торги</td></tr></table>"
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_public_offers(client=client)
    assert result[0]["trade_number"] == "333593"


@pytest.mark.asyncio
async def test_public_offer_html_fixture_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(efrsb_public_url="https://bankrot.fedresurs.ru"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "public_offer"
        return httpx.Response(
            200,
            text=(
                '<div class="offer-item">'
                '<a href="/lot/42">Право требования</a>'
                '<span class="price">12 345,67 руб.</span>'
                "</div>"
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_public_offers(client=client)

    assert result == [
        {
            "title": "Право требования",
            "url": "https://bankrot.fedresurs.ru/lot/42",
            "price_text": "12 345,67 руб.",
            "date_text": "",
            "source_page": 1,
        }
    ]


@pytest.mark.asyncio
async def test_public_offer_challenge_is_not_an_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep this regression test independent from a developer's local CDP
    # profile: it verifies the HTTP challenge error when browser fallback is
    # unavailable, rather than making a real browser request.
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(
            efrsb_public_url="https://bankrot.fedresurs.ru",
            cloakbrowser_cdp_url="",
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceAccessError, match="status=403"):
            await search_public_offers(client=client)


@pytest.mark.asyncio
async def test_public_offer_transport_error_uses_cloakbrowser_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(
            efrsb_public_url="https://bankrot.fedresurs.ru",
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=20,
            cloakbrowser_wait_seconds=1,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def fallback(url: str, timeout: float) -> str:
        assert "/publications/public/offer" in url
        assert timeout == 20
        return (
            '<div class="offer-item">'
            '<a href="/lot/42">Право требования</a>'
            '<span class="price">12 345,67 руб.</span>'
            "</div>"
        )

    monkeypatch.setattr("src.connectors.efrsb._fetch_via_cloakbrowser", fallback)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_public_offers(client=client)

    assert result[0]["url"] == "https://bankrot.fedresurs.ru/lot/42"


def test_extract_debtor_inn_prefers_role_context_and_exclusions():
    description = (
        "Банкрот ООО Продавец ИНН 7700000009. "
        "Право требования к ООО Покупатель ИНН 7707083893."
    )
    assert extract_debtor_inn(description, None) == "7707083893"
    assert extract_debtor_inn("ИНН 7700000009 ИНН 7707083893", None, {"7700000009"}) == "7707083893"


def test_parse_price_intervals_marks_current_step():
    html = """
    <table class="price-reduction">
      <tr><th>Период</th><th>Цена</th></tr>
      <tr><td>01.01.2026 00:00 — 03.01.2026 00:00</td><td>100 000 руб.</td></tr>
      <tr><td>03.01.2026 00:00 — 05.01.2026 00:00</td><td>80 000 руб.</td></tr>
    </table>
    """
    intervals = parse_price_intervals(
        html,
        now=datetime(2026, 1, 2, 12, tzinfo=UTC),
    )
    assert len(intervals) == 2
    assert intervals[0]["price"] == Decimal("100000")
    assert intervals[0]["is_current"] is True
    assert intervals[1]["is_current"] is False
    assert intervals[0]["starts_at"] == datetime(2025, 12, 31, 21, tzinfo=UTC)
    assert intervals[0]["ends_at"] == datetime(2026, 1, 2, 21, tzinfo=UTC)


def test_parse_price_intervals_has_no_current_step_outside_schedule():
    html = """
    <table>
      <tr><td>01.09.2026 00:00 — 03.09.2026 00:00</td><td>10 000 руб.</td></tr>
    </table>
    """
    intervals = parse_price_intervals(html, now=datetime(2026, 8, 28, tzinfo=UTC))
    assert intervals[0]["is_current"] is False


def test_parse_price_intervals_does_not_use_sequence_as_price():
    html = """
    <table>
      <tr><td>1</td><td>01.09.2026 — 03.09.2026</td><td>цена не указана</td></tr>
    </table>
    """
    assert parse_price_intervals(html, now=datetime(2026, 9, 1, tzinfo=UTC)) == []


def test_public_offer_parser_rejects_generic_page_without_rows():
    import asyncio
    from types import SimpleNamespace

    import src.connectors.efrsb as efrsb

    class Response:
        status_code = 200
        text = "<html><body>Торги публичного предложения</body></html>"
        url = "https://bankrot.fedresurs.ru/publications/public/offer"

        def raise_for_status(self):
            return None

    class Client:
        async def get(self, *_args, **_kwargs):
            return Response()

    async def run():
        from src.connectors.efrsb import SourceParseError, search_public_offers

        old = efrsb.get_settings
        efrsb.get_settings = lambda: SimpleNamespace(efrsb_public_url="https://bankrot.fedresurs.ru")
        try:
            with pytest.raises(SourceParseError):
                await search_public_offers(client=Client())
        finally:
            efrsb.get_settings = old

    asyncio.run(run())


def test_parse_moscow_time_is_converted_to_utc():
    from src.connectors.efrsb import _parse_datetime

    assert _parse_datetime("01.09.2026 12:00 МСК") == datetime(
        2026, 9, 1, 9, tzinfo=UTC
    )


def test_moscow_timezone_is_preserved_for_interval_cells():
    from src.connectors.efrsb import _parse_datetimes

    assert _parse_datetimes("01.09.2026 12:00 МСК — 02.09.2026 12:00 МСК") == [
        datetime(2026, 9, 1, 9, tzinfo=UTC),
        datetime(2026, 9, 2, 9, tzinfo=UTC),
    ]


def test_parse_lot_card_exposes_price_reduction_and_lot_number():
    html = """
    <h1>Лот №42 — право требования</h1>
    <div class="description">ИНН 7707083893</div>
    <div class="price-reduction"><table>
      <tr><td>01.01.2026 — 03.01.2026</td><td>10 000 руб.</td></tr>
    </table></div>
    """
    result = parse_lot_card(html, "https://bankrot.fedresurs.ru/lot/42")
    assert result["lot_no"] == 42
    assert result["price_reduction_html"]
    assert result["price_intervals"][0]["price"] == Decimal("10000")


def test_legacy_trade_card_extracts_subject_price_and_interval_schedule() -> None:
    result = parse_lot_card(
        """
        <html><body>
          <h1>Карточка торгов</h1>
          <table>
            <tr><th>Вид торгов</th><td>Публичное предложение</td></tr>
            <tr><th>Предмет торгов</th><td>Право требования к ООО Ромашка ИНН 7707083893</td></tr>
            <tr><th>Начальная цена, руб.</th><td>1 765 953,90</td></tr>
            <tr><th>Статус торгов</th><td>Открыт прием заявок</td></tr>
          </table>
          <h2>Лот № 1</h2>
          <table>
            <tr><th>Дата начала приема заявок на интервале</th><th>Дата окончания интервала</th><th>Цена на интервале, руб.</th></tr>
            <tr><td>01.01.2026 00:00</td><td>03.01.2026 00:00</td><td>1 000 000,00</td></tr>
          </table>
        </body></html>
        """,
        "https://old.bankrot.fedresurs.ru/TradeCard.aspx?ID=1",
    )
    assert result["lot_no"] == 1
    assert result["description"].startswith("Право требования")
    assert result["start_price"] == Decimal("1765953.90")
    assert result["price_intervals"][0]["price"] == Decimal("1000000.00")


def test_parse_lot_card_connects_etp_and_documents():
    result = parse_lot_card(
        """
        <h1>Лот №42 — право требования</h1>
        <a href="https://elektortorgi.ru/trade/abc-42/lot/1">ЭТП</a>
        <a href="https://elektortorgi.ru/files/contract.pdf">Договор</a>
        """,
        "https://bankrot.fedresurs.ru/lot/42",
    )
    assert result["etp_url"] == "https://elektortorgi.ru/trade/abc-42/lot/1"
    assert result["trade_id_on_etp"] == "abc-42"
    assert result["documents"][0]["url"].endswith("contract.pdf")


def test_parse_lot_card_rejects_unrecognized_empty_markup():
    with pytest.raises(SourceParseError, match="markers were not found"):
        parse_lot_card("<html><div class='new-layout'>changed</div></html>", "https://bankrot.fedresurs.ru/lot/42")


def test_parse_lot_card_uses_sberbank_query_trade_id_and_ignores_document_as_etp():
    result = parse_lot_card(
        """
        <a href="https://utp.sberbank-ast.ru/files/contract.pdf">Договор</a>
        <a href="https://utp.sberbank-ast.ru/bankrupttrade/TradeCard.aspx?tid=42&amp;lid=1">ЭТП</a>
        """,
        "https://bankrot.fedresurs.ru/lot/42",
    )
    assert result["etp_url"].endswith("tid=42&lid=1")
    assert result["trade_id_on_etp"] == "42"


@pytest.mark.asyncio
async def test_public_offer_uses_cloakbrowser_after_http_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(
            efrsb_public_url="https://bankrot.fedresurs.ru",
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=30,
            cloakbrowser_wait_seconds=0,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async def browser_fetch(url: str, timeout: float) -> str:
        assert "page=1" in url and "type=public_offer" in url
        assert timeout == 30
        return '<div class="offer-item"><a href="/lot/99">Долг</a><span class="price">1 000 руб.</span></div>'

    monkeypatch.setattr("src.connectors.efrsb._fetch_via_cloakbrowser", browser_fetch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_public_offers(client=client)

    assert result[0]["url"] == "https://bankrot.fedresurs.ru/lot/99"


def test_debtor_inn_skips_bankrupt_party_context():
    description = (
        "Должник (банкрот) ООО Продавец ИНН 7700000009. "
        "Право требования к ООО Дебитор ИНН 7707083893."
    )
    assert extract_debtor_inn(description, None) == "7707083893"


def test_debtor_inn_does_not_fallback_to_bankrupt_party():
    description = "Должник (банкрот) ООО Продавец ИНН 7700000009"
    assert extract_debtor_inn(description, None) is None


@pytest.mark.asyncio
async def test_public_offer_uses_cloakbrowser_for_http_200_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(
            efrsb_public_url="https://bankrot.fedresurs.ru",
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=30,
            cloakbrowser_wait_seconds=0,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>CAPTCHA</html>", request=request)

    async def browser_fetch(url: str, timeout: float) -> str:
        assert "type=public_offer" in url
        return '<div class="offer-item"><a href="/lot/100">Долг</a></div>'

    monkeypatch.setattr("src.connectors.efrsb._fetch_via_cloakbrowser", browser_fetch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_public_offers(client=client)
    assert result[0]["url"].endswith("/lot/100")
