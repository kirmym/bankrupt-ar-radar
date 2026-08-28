"""Тесты ЭТП-парсера файлов."""
from __future__ import annotations

import httpx
import pytest

from src.connectors.etp_base import EtpFile
from src.connectors.etp_cdt import CdtAdapter
from src.connectors.files import (
    extract_dates,
    extract_facts_from_text,
    extract_inn_from_text,
    extract_ogrn_from_text,
    extract_sums,
    extract_text_from_pdf,
    propose_fact_updates,
)
from src.connectors.llm import validate_llm_facts
from src.models.enums import TradeStatus
from src.workers.etp_worker import normalize_trade_status
from src.workers.files_worker import adapter_for_document_url


def test_extract_inn_basic():
    text = "ООО Ромашка ИНН 7701234567"
    inns = extract_inn_from_text(text)
    assert "7701234567" in inns


def test_extract_inn_unique():
    text = "ИНН 7701234567 ИНН 7701234567"
    inns = extract_inn_from_text(text)
    assert len(inns) == 1


def test_extract_ogrn():
    text = "ОГРН 1027700132195"
    ogrns = extract_ogrn_from_text(text)
    assert "1027700132195" in ogrns


def test_extract_dates():
    text = "Договор от 01.09.2023, акт от 15.10.2023"
    dates = extract_dates(text)
    assert "01.09.2023" in dates
    assert "15.10.2023" in dates


def test_extract_sums():
    text = "Задолженность 1 234 567,89 руб. пени 100 000,00"
    sums = extract_sums(text)
    assert "1 234 567,89" in sums
    assert "100 000,00" in sums


def test_extract_facts_judgment():
    text = "Решением Арбитражного суда г. Москвы от 15.10.2023 иск удовлетворён"
    facts = extract_facts_from_text(text)
    assert facts["has_judgment"] is True


def test_extract_facts_writ():
    text = "Выдан исполнительный лист серия ВС №012345678"
    facts = extract_facts_from_text(text)
    assert facts["has_writ"] is True


def test_extract_facts_secured():
    text = "Исполнение обеспечено залогом имущества должника"
    facts = extract_facts_from_text(text)
    assert facts["has_secured"] is True


def test_extract_facts_assignment_forbidden():
    text = "Уступка права требования без согласия должника не допускается"
    facts = extract_facts_from_text(text)
    assert facts["has_assignment_forbidden"] is True


def test_extract_facts_respects_simple_negations():
    facts = extract_facts_from_text(
        "Решения суда нет; исполнительный лист не выдан; без залога; уступка разрешена."
    )
    assert facts["has_judgment"] is False
    assert facts["has_writ"] is False
    assert facts["has_secured"] is False
    assert facts["has_assignment_forbidden"] is False


def test_extract_facts_court_case():
    text = "В рамках дела А40-12345/2023 установлено следующее"
    facts = extract_facts_from_text(text)
    assert facts["court_case"] == "А40-12345/2023"


def test_extract_text_from_pdf_no_pypdf():
    """Если pdfplumber/pypdf не установлен, возвращает пустую строку."""
    result = extract_text_from_pdf(b"%PDF-1.4 fake")
    # Зависит от того, что установлено; просто не падает
    assert isinstance(result, str)


def test_extract_text_passthrough_text():
    from src.connectors.files import extract_text

    text = "Hello world"
    result = extract_text(text.encode("utf-8"), "text/plain")
    assert "Hello" in result


def test_llm_facts_are_validated_before_storage():
    valid = validate_llm_facts(
        {
            "debtor": {"inn": "7701234567", "name": "ООО Ромашка"},
            "claim": {"kind": "trade_ar", "principal": 1000},
        }
    )
    assert valid == {
        "debtor": {"inn": "7701234567", "name": "ООО Ромашка"},
        "claim": {"kind": "trade_ar", "principal": "1000"},
    }
    assert validate_llm_facts({"debtor": {"inn": "bad"}}) is None


def test_fact_proposal_preserves_conflicts_for_manual_review():
    from types import SimpleNamespace

    proposal = propose_fact_updates(
        {"claim": {"principal": "200", "has_writ": True}, "debtor": {"inn": "7701234567"}},
        claim=SimpleNamespace(principal="100", has_writ=False),
        debtor=SimpleNamespace(inn=None),
    )
    assert proposal["requires_review"] is True
    assert "claim.principal" in proposal["conflicts"]
    assert proposal["updates"]["debtor"]["inn"] == "7701234567"


def test_document_adapter_is_selected_by_allowlisted_host():
    assert adapter_for_document_url("https://elektortorgi.ru/file.pdf") is CdtAdapter
    assert adapter_for_document_url("https://utp.sberbank-ast.ru/File/a.pdf").__name__ == "SberbankAdapter"
    assert adapter_for_document_url("https://example.invalid/file.pdf") is None


def test_etp_status_normalization_is_conservative():
    assert normalize_trade_status("Торги отменены") == TradeStatus.CANCELLED.value
    assert normalize_trade_status("unknown vendor label") is None


@pytest.mark.asyncio
async def test_downloader_rejects_private_ip():
    adapter = CdtAdapter()
    adapter.allowed_hosts = {"127.0.0.1"}
    with pytest.raises(ValueError, match="non-public"):
        await adapter._validate_download_url("http://127.0.0.1/document.pdf")


@pytest.mark.asyncio
async def test_downloader_rejects_declared_oversize(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(26 * 1024 * 1024)})

    adapter = CdtAdapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(adapter, "_validate_download_url", lambda _url: _noop())
    try:
        with pytest.raises(ValueError, match="size limit"):
            await adapter.download_file(
                EtpFile(title="large", url="https://elektortorgi.ru/file", kind="other")
            )
    finally:
        await adapter._client.aclose()


async def _noop() -> None:
    return None
