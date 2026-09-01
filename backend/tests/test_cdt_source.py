"""Contract tests for the public CDT seed connector."""
from datetime import UTC, datetime
from decimal import Decimal

from src.config import Settings
from src.connectors.cdt_source import _trade_status, parse_cdt_detail, parse_cdt_schedule
from src.models.enums import TradeStatus, is_participable_trade_status


def test_source_proxy_is_optional_and_trimmed() -> None:
    assert Settings(source_proxy_url="  http://109.94.1.23:4050  ").source_proxy == (
        "http://109.94.1.23:4050"
    )
    assert Settings(source_proxy_url="").source_proxy is None


def test_parse_cdt_schedule_marks_current_interval() -> None:
    rows = [
        {
            "startTime": "05.10.2026 09:00:00",
            "endTime": "09.10.2026 18:00:00",
            "price": "5763233,23",
        },
        {
            "startTime": "10.10.2026 09:00:00",
            "endTime": "12.10.2026 18:00:00",
            "price": "4500000,00",
        },
    ]

    parsed = parse_cdt_schedule(rows, now=datetime(2026, 10, 10, 9, tzinfo=UTC))

    assert [row["price"] for row in parsed] == [Decimal("5763233.23"), Decimal("4500000.00")]
    assert [row["is_current"] for row in parsed] == [False, True]


def test_trade_status_gate_accepts_only_participable_states() -> None:
    assert _trade_status("Объявлены торги") == TradeStatus.ANNOUNCED.value
    assert _trade_status("Идёт приём заявок") == TradeStatus.APPLICATIONS_OPEN.value
    assert _trade_status("Идут торги") == TradeStatus.IN_PROGRESS.value
    assert not is_participable_trade_status(_trade_status("Подведение итогов"))
    assert not is_participable_trade_status(_trade_status("Торги отменены"))
    assert not is_participable_trade_status(_trade_status("неизвестная стадия"))


def test_parse_cdt_detail_builds_seed_card() -> None:
    payload = {
        "tradeId": 372159,
        "name": "Продажа дебиторской задолженности",
        "tradeStatusDescription": "Прием заявок",
        "requestTimeBegin": "05.10.2026 09:00",
        "requestTimeEnd": "11.11.2026 18:00",
        "dealNum": "А55-34889/2023",
        "debtJurINN": "6316233122",
        "debtShortName": 'ООО "Техпроминвест"',
        "orgName": "Организатор",
        "arbManLastName": "Иванов",
        "lot": {
            "name": "Право требования к ООО Дебитор",
            "lotInfo": (
                "<p>Право требования к ООО Дебитор, ИНН 7707083893, "
                "в размере 6 196 476,00 руб.</p>"
            ),
            "lotNumber": 1,
            "priceBegin": "5763233,23",
            "tradeLotId": 372690,
            "categoryIDs": [52],
            "lotScheduleItems": [
                {
                    "startTime": "05.10.2026 09:00:00",
                    "endTime": "09.10.2026 18:00:00",
                    "price": "5763233,23",
                }
            ],
        },
    }

    card = parse_cdt_detail(payload, now=datetime(2026, 9, 1, tzinfo=UTC))

    assert card is not None
    assert card["source_name"] == "cdt_public"
    assert card["efrsb_url"] == "https://torgi.cdtrf.ru/trades/372159"
    assert card["start_price"] == Decimal("5763233.23")
    assert card["nominal_claimed"] == Decimal("6196476.00")
    assert card["debtor_inn"] == "7707083893"
    assert card["bankrupt_inn"] == "6316233122"
    assert card["price_schedule_status"] == "not_started"


def test_parse_cdt_detail_rejects_category_false_positive() -> None:
    payload = {
        "tradeId": 372773,
        "lot": {
            "name": "1/5 доля в жилом помещении",
            "lotInfo": "<p>Квартира площадью 26,7 кв.м.</p>",
            "categoryIDs": [53],
        },
    }

    assert parse_cdt_detail(payload) is None


def test_parse_cdt_detail_extracts_general_total_and_accepts_naive_now() -> None:
    payload = {
        "tradeId": 123,
        "requestTimeBegin": "01.09.2026 09:00",
        "lot": {
            "name": "Право требования в общем размере 124 005 470,04 руб.",
            "lotInfo": "<p>Дебиторская задолженность</p>",
            "priceBegin": "1000000",
        },
    }

    card = parse_cdt_detail(payload, now=datetime(2026, 9, 1, 12, 0))  # noqa: DTZ001

    assert card is not None
    assert card["nominal_claimed"] == Decimal("124005470.04")
    assert card["price_observed_at"].tzinfo is UTC
