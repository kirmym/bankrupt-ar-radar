"""Тесты для ИНН-экстрактора."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from src.connectors.efrsb import (
    SourceAccessError,
    extract_debtor_inn,
    extract_inn,
    extract_ogrn,
    parse_lot_card,
    parse_price_intervals,
    search_public_offers,
)


def test_extract_inn_10_digits():
    text = "ООО Ромашка ИНН 7701234567 зарегистрировано"
    result = extract_inn(text)
    assert "7701234567" in result


def test_extract_inn_12_digits():
    text = "ИП Иванов ИНН 123456789012"
    result = extract_inn(text)
    assert "123456789012" in result


def test_extract_inn_no_inn():
    assert extract_inn("нет инн") == []


def test_extract_inn_filters_invalid():
    text = "ИНН 0123456789"  # 10 знаков, начинается с 0 — невалидный
    result = extract_inn(text)
    assert "0123456789" not in result


def test_extract_inn_unique():
    text = "ИНН 7701234567 ИНН 7701234567"
    result = extract_inn(text)
    assert len(result) == 1


def test_extract_ogrn_13():
    text = "ОГРН 1027700132195"
    result = extract_ogrn(text)
    assert "1027700132195" in result


def test_extract_debtor_inn_with_label():
    text = "Право требования к ООО «Ромашка» ИНН 7701234567"
    inn = extract_debtor_inn(text, None)
    assert inn == "7701234567"


def test_extract_debtor_inn_with_title():
    text = "Дебиторская задолженность ООО Ромашка ОГРН 1027700132195"
    title = "Лот №1: 7701234567"
    inn = extract_debtor_inn(text, title)
    assert inn == "7701234567"


def test_extract_debtor_inn_empty():
    assert extract_debtor_inn(None, None) is None


def test_extract_debtor_inn_no_match():
    text = "Просто текст без цифр"
    assert extract_debtor_inn(text, None) is None


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
async def test_public_offer_challenge_is_not_an_empty_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceAccessError, match="status=403"):
            await search_public_offers(client=client)


def test_extract_debtor_inn_prefers_role_context_and_exclusions():
    description = (
        "Банкрот ООО Продавец ИНН 7700000000. "
        "Право требования к ООО Покупатель ИНН 7701234567."
    )
    assert extract_debtor_inn(description, None) == "7701234567"
    assert extract_debtor_inn("ИНН 7700000000 ИНН 7701234567", None, {"7700000000"}) == "7701234567"


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
    assert intervals[0]["starts_at"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert intervals[0]["ends_at"] == datetime(2026, 1, 3, tzinfo=UTC)


def test_parse_lot_card_exposes_price_reduction_and_lot_number():
    html = """
    <h1>Лот №42 — право требования</h1>
    <div class="description">ИНН 7701234567</div>
    <div class="price-reduction"><table>
      <tr><td>01.01.2026 — 03.01.2026</td><td>10 000 руб.</td></tr>
    </table></div>
    """
    result = parse_lot_card(html, "https://bankrot.fedresurs.ru/lot/42")
    assert result["lot_no"] == 42
    assert result["price_reduction_html"]
    assert result["price_intervals"][0]["price"] == Decimal("10000")


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
        assert url.endswith("/publications/public/offer")
        assert timeout == 30
        return '<div class="offer-item"><a href="/lot/99">Долг</a><span class="price">1 000 руб.</span></div>'

    monkeypatch.setattr("src.connectors.efrsb._fetch_via_cloakbrowser", browser_fetch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_public_offers(client=client)

    assert result[0]["url"] == "https://bankrot.fedresurs.ru/lot/99"
