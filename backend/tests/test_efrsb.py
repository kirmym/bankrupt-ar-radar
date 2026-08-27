"""Тесты для ИНН-экстрактора."""
from __future__ import annotations

from src.connectors.efrsb import extract_debtor_inn, extract_inn, extract_ogrn


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
