"""Тесты ЭТП-парсера файлов."""
from __future__ import annotations

from src.connectors.files import (
    extract_dates,
    extract_facts_from_text,
    extract_inn_from_text,
    extract_ogrn_from_text,
    extract_sums,
    extract_text_from_pdf,
)


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
