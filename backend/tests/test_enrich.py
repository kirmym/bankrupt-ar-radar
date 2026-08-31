"""Regression tests for free API and HTML parser source policy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.models.entities import Party
from src.models.enums import PartyRole


@pytest.mark.asyncio
async def test_egrul_html_parser_sets_active_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.connectors import enrich

    async def no_rows(_inn: str) -> None:
        return None

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(free_api_sources_list=set()),
    )
    monkeypatch.setattr(enrich, "_fetch_egrul_rows", no_rows)
    async def fake_html(_url: str) -> str:
        return _html(
            '<div class="org-name">ООО Ромашка</div>'
            "Статус: Действующая ОГРН 1027700132195"
        )

    monkeypatch.setattr(enrich, "_fetch_html", fake_html)
    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")

    assert await enrich.enrich_from_egrul(party, None) is True
    assert party.name == "ООО Ромашка"
    assert party.status == "active"
    assert party.ogrn == "1027700132195"
    assert party.source_as_of is not None


@pytest.mark.asyncio
async def test_html_parser_uses_browser_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.connectors import enrich

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *_args, **_kwargs):
            raise enrich.httpx.ConnectError("offline")

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(
            free_api_sources_list=set(),
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=5,
            cloakbrowser_wait_seconds=0,
        ),
    )
    monkeypatch.setattr(enrich.httpx, "AsyncClient", lambda **_kwargs: FailingClient())
    calls: list[tuple[str, str]] = []

    async def browser_html(url: str, **kwargs) -> str:
        calls.append((url, kwargs["cdp_url"]))
        return "<html><body>ok</body></html>"

    monkeypatch.setattr(enrich, "fetch_html_via_cloakbrowser", browser_html)

    result = await enrich._fetch_html("https://egrul.nalog.ru/index.html?query=7707083893")

    assert result == "<html><body>ok</body></html>"
    assert calls == [("https://egrul.nalog.ru/index.html?query=7707083893", "http://127.0.0.1:9222")]


@pytest.mark.asyncio
async def test_fssp_parser_is_used_when_api_is_not_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.connectors import enrich

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(
            free_api_sources_list=set(),
            cloakbrowser_cdp_url="",
        ),
    )
    async def fake_html(_url: str) -> str:
        return _html("Исполнительные производства: 1 234,50 руб.")

    monkeypatch.setattr(enrich, "_fetch_html", fake_html)
    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")

    assert await enrich.enrich_from_fssp(party, None) is True
    assert party.fssp_sum == 1234.50
    # EGRUL is the only source allowed to refresh the shared identity timestamp.
    assert party.source_as_of is None


@pytest.mark.asyncio
async def test_egrul_generic_search_page_does_not_refresh_old_party(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.connectors import enrich

    async def no_rows(_inn: str) -> None:
        return None

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(free_api_sources_list=set()),
    )
    monkeypatch.setattr(enrich, "_fetch_egrul_rows", no_rows)

    async def fake_html(_url: str) -> str:
        return _html("<h1>Поиск ЕГРЮЛ</h1><p>Статус: действующая</p><p>Введите ИНН</p>")

    monkeypatch.setattr(enrich, "_fetch_html", fake_html)
    old_timestamp = datetime.now(UTC) - timedelta(days=3)
    party = Party(
        role=PartyRole.DEBTOR.value,
        inn="7707083893",
        status="active",
        source_as_of=old_timestamp,
    )

    assert await enrich.enrich_from_egrul(party, None) is False
    assert party.status == "active"
    assert party.source_as_of == old_timestamp


@pytest.mark.asyncio
async def test_egrul_public_search_row_sets_identity_without_guessing_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.connectors import enrich

    async def rows(_inn: str) -> list[dict[str, object]]:
        return [{"i": "7707083893", "n": "ООО Ромашка", "o": "1027700132195"}]

    monkeypatch.setattr(enrich, "_fetch_egrul_rows", rows)
    async def no_html(_url: str) -> None:
        return None

    monkeypatch.setattr(enrich, "_fetch_html", no_html)
    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")

    assert await enrich.enrich_from_egrul(party, None) is True
    assert party.name == "ООО Ромашка"
    assert party.ogrn == "1027700132195"
    assert party.status is None


@pytest.mark.asyncio
async def test_egrul_public_search_row_applies_status_when_source_supplies_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.connectors import enrich

    async def rows(_inn: str) -> list[dict[str, object]]:
        return [{"i": "7707083893", "n": "ООО Ромашка", "o": "1027700132195", "s": "Действующая"}]

    monkeypatch.setattr(enrich, "_fetch_egrul_rows", rows)
    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")

    assert await enrich.enrich_from_egrul(party, None) is True
    assert party.status == "active"


def test_egrul_extract_parser_marks_explicit_adverse_flags() -> None:
    from src.connectors.enrich import _parse_egrul_extract

    parsed = _parse_egrul_extract(
        "Сведения недостоверны. Адрес юридического лица недостоверен. "
        "Решение о предстоящем исключении ЮЛ из ЕГРЮЛ."
    )

    assert parsed["status"] == "invalid"
    assert parsed["invalid_address"] is True
    assert parsed["pending_exclusion"] is True


def test_egrul_extract_parser_does_not_treat_pending_exclusion_as_completed() -> None:
    from src.connectors.enrich import _parse_egrul_extract

    parsed = _parse_egrul_extract("Решение о предстоящем исключении ЮЛ из ЕГРЮЛ")

    assert parsed["pending_exclusion"] is True
    assert parsed["status"] is None


def test_egrul_extract_parser_does_not_mark_negative_liquidation_phrase() -> None:
    from src.connectors.enrich import _parse_egrul_extract

    parsed = _parse_egrul_extract("Юридическое лицо не находится в процессе ликвидации")

    assert parsed["status"] is None


@pytest.mark.asyncio
async def test_fssp_no_results_are_not_marked_uncollectible(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.connectors import enrich

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(free_api_sources_list=set(), cloakbrowser_cdp_url=""),
    )

    async def fake_html(_url: str) -> str:
        return _html("Исполнительные производства не найдены")

    monkeypatch.setattr(enrich, "_fetch_html", fake_html)
    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")

    assert await enrich.enrich_from_fssp(party, None) is True
    assert party.fssp_sum == 0
    assert party.fssp_uncollectible is False


@pytest.mark.asyncio
async def test_fssp_api_uses_legal_endpoint_for_ten_digit_inn(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.connectors import enrich

    requested: list[str] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"result": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str, **kwargs):
            requested.append(url)
            return FakeResponse()

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(
            free_api_sources_list={"fssp"},
            fssp_api_url="https://api-ip.fssprus.ru",
            fssp_api_token="",
        ),
    )
    monkeypatch.setattr(enrich.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")
    assert await enrich.enrich_from_fssp(party, None) is True
    assert requested == ["https://api-ip.fssprus.ru/api/v1.0/search/legal"]


@pytest.mark.asyncio
async def test_kad_parser_does_not_use_navigation_text_as_bankruptcy_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.connectors import enrich

    monkeypatch.setattr(
        enrich,
        "get_settings",
        lambda: SimpleNamespace(free_api_sources_list=set(), cloakbrowser_cdp_url=""),
    )

    async def fake_html(_url: str) -> str:
        return _html(
            "<nav>Банкротство</nav>"
            "<p>Всего дел: 1</p>"
            "<table><tr><td>А40-123/2023</td><td>Взыскание долга</td></tr></table>"
        )

    monkeypatch.setattr(enrich, "_fetch_html", fake_html)
    party = Party(role=PartyRole.DEBTOR.value, inn="7707083893")

    assert await enrich.enrich_from_kad(party, None) is True
    assert party.kad_as_defendant_count == 1
    assert party.kad_bankruptcy_open is False


def _html(body: str) -> str:
    return f"<html><body>{body}</body></html>"
