"""Тесты ЭТП-парсера файлов."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.connectors.etp_base import EtpAccessError, EtpFile
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
from src.models.entities import Document
from src.models.enums import TradeStatus
from src.workers.etp_worker import normalize_trade_status
from src.workers.files_worker import EfrsbDocumentAdapter, adapter_for_document_url, process_file


def test_extract_inn_basic():
    text = "ООО Ромашка ИНН 7707083893"
    inns = extract_inn_from_text(text)
    assert "7707083893" in inns


def test_extract_inn_unique():
    text = "ИНН 7707083893 ИНН 7707083893"
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
            "debtor": {"inn": "7707083893", "name": "ООО Ромашка"},
            "claim": {"kind": "trade_ar", "principal": 1000},
        }
    )
    assert valid == {
        "debtor": {"inn": "7707083893", "name": "ООО Ромашка"},
        "claim": {"kind": "trade_ar", "principal": "1000"},
    }
    assert validate_llm_facts({"debtor": {"inn": "bad"}}) is None


def test_fact_proposal_preserves_conflicts_for_manual_review():
    from types import SimpleNamespace

    proposal = propose_fact_updates(
        {"claim": {"principal": "200", "has_writ": True}, "debtor": {"inn": "7707083893"}},
        claim=SimpleNamespace(principal="100", has_writ=False),
        debtor=SimpleNamespace(inn=None),
    )
    assert proposal["requires_review"] is True
    assert "claim.principal" in proposal["conflicts"]
    assert proposal["updates"]["debtor"]["inn"] == "7707083893"


def test_document_adapter_is_selected_by_allowlisted_host():
    assert adapter_for_document_url("https://elektortorgi.ru/file.pdf") is CdtAdapter
    assert adapter_for_document_url("https://utp.sberbank-ast.ru/File/a.pdf").__name__ == "SberbankAdapter"
    assert adapter_for_document_url("https://example.invalid/file.pdf") is None


def test_document_adapter_supports_efrsb_public_documents():
    assert adapter_for_document_url("https://bankrot.fedresurs.ru/files/contract.pdf") is EfrsbDocumentAdapter


@pytest.mark.asyncio
async def test_legacy_doc_is_downloaded_but_left_for_manual_review(monkeypatch: pytest.MonkeyPatch):
    class FakeAdapter:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def download_file(self, _file):
            return b"legacy binary"

    monkeypatch.setattr(
        "src.workers.files_worker.adapter_for_document_url",
        lambda _url: FakeAdapter,
    )
    doc = Document(lot_id=1, url="https://bankrot.fedresurs.ru/files/contract.doc", title="Договор")
    result = await process_file(doc, 1, None)
    assert result["status"] == "needs_review"
    assert doc.downloaded_at is None
    assert doc.text is None


def test_etp_status_normalization_is_conservative():
    assert normalize_trade_status("Торги отменены") == TradeStatus.CANCELLED.value
    assert normalize_trade_status("Торги не состоялись") == TradeStatus.DID_NOT_TAKE_PLACE.value
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


@pytest.mark.asyncio
async def test_etp_html_uses_cloakbrowser_after_challenge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "src.connectors.etp_base.get_settings",
        lambda: SimpleNamespace(
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=30,
            cloakbrowser_wait_seconds=0,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async def browser_fetch(url: str, **kwargs) -> str:
        assert url.startswith("https://elektortorgi.ru/")
        assert kwargs["allowed_hosts"] == {"elektortorgi.ru"}
        return "<html><body>ok</body></html>"

    monkeypatch.setattr(
        "src.connectors.cloakbrowser.fetch_html_via_cloakbrowser",
        browser_fetch,
    )
    adapter = CdtAdapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        status, html = await adapter.fetch_html("https://elektortorgi.ru/trade/1/lot/1")
    finally:
        await adapter._client.aclose()
    assert status == 200
    assert "ok" in html


@pytest.mark.asyncio
async def test_etp_html_stays_paused_without_cloakbrowser(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "src.connectors.etp_base.get_settings",
        lambda: SimpleNamespace(cloakbrowser_cdp_url=""),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    adapter = CdtAdapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(EtpAccessError, match="status=429"):
            await adapter.fetch_html("https://elektortorgi.ru/trade/1/lot/1")
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_etp_html_uses_cloakbrowser_for_http_200_challenge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "src.connectors.etp_base.get_settings",
        lambda: SimpleNamespace(
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=30,
            cloakbrowser_wait_seconds=0,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Verify you are human</html>", request=request)

    async def browser_fetch(url: str, **kwargs) -> str:
        assert kwargs["allowed_hosts"] == {"elektortorgi.ru"}
        return "<html><body>real page</body></html>"

    monkeypatch.setattr("src.connectors.cloakbrowser.fetch_html_via_cloakbrowser", browser_fetch)
    adapter = CdtAdapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        status, html = await adapter.fetch_html("https://elektortorgi.ru/trade/1/lot/1")
    finally:
        await adapter._client.aclose()
    assert status == 200
    assert "real page" in html



@pytest.mark.asyncio
async def test_downloader_uses_cloakbrowser_after_challenge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "src.connectors.etp_base.get_settings",
        lambda: SimpleNamespace(
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=30,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async def browser_download(url: str, **kwargs) -> bytes:
        assert url.endswith("document.pdf")
        assert kwargs["max_bytes"] == 25 * 1024 * 1024
        return b"%PDF-1.4 content"

    monkeypatch.setattr(
        "src.connectors.cloakbrowser.fetch_bytes_via_cloakbrowser",
        browser_download,
    )
    adapter = CdtAdapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(adapter, "_validate_download_url", lambda _url: _noop())
    try:
        data = await adapter.download_file(
            EtpFile(title="document", url="https://elektortorgi.ru/document.pdf", kind="прочее")
        )
    finally:
        await adapter._client.aclose()
    assert data.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_downloader_uses_cloakbrowser_for_http_200_challenge(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "src.connectors.etp_base.get_settings",
        lambda: SimpleNamespace(
            cloakbrowser_cdp_url="http://127.0.0.1:9222",
            cloakbrowser_timeout_seconds=30,
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>CAPTCHA</html>",
            request=request,
        )

    async def browser_download(url: str, **kwargs) -> bytes:
        assert url.endswith("document.pdf")
        return b"%PDF-1.4 content"

    monkeypatch.setattr(
        "src.connectors.cloakbrowser.fetch_bytes_via_cloakbrowser",
        browser_download,
    )
    adapter = CdtAdapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(adapter, "_validate_download_url", lambda _url: _noop())
    try:
        data = await adapter.download_file(
            EtpFile(title="document", url="https://elektortorgi.ru/document.pdf", kind="прочее")
        )
    finally:
        await adapter._client.aclose()
    assert data.startswith(b"%PDF")
