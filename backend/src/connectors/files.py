"""Парсер файлов лота — PDF, DOCX, OCR."""
from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")
SUM_RE = re.compile(r"(\d{1,3}(?:[ \u00a0]\d{3})*[.,]\d{2})")

# Дата в формате ДД.ММ.ГГГГ
DATE_RE = re.compile(r"\b(\d{2}[.\\/]\d{2}[.\\/]\d{4})\b")


def extract_text_from_pdf(data: bytes) -> str:
    """Извлекает текст из PDF. Использует pdfplumber, иначе pypdf."""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass
    return ""


def extract_text_from_docx(data: bytes) -> str:
    """Извлекает текст из DOCX."""
    try:
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    except ImportError:
        return ""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_inn_from_text(text: str) -> list[str]:
    """Извлекает все ИНН из текста."""
    seen: set[str] = set()
    result: list[str] = []
    for m in INN_RE.findall(text):
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def extract_ogrn_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for m in OGRN_RE.findall(text):
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def extract_dates(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for m in DATE_RE.findall(text):
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def extract_sums(text: str) -> list[str]:
    """Суммы в рублях."""
    seen: set[str] = set()
    result: list[str] = []
    for m in SUM_RE.findall(text):
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def extract_facts_from_text(text: str) -> dict:
    """Извлекает факты из текста документа.

    Возвращает:
    - inn: список ИНН
    - ogrn: список ОГРН
    - dates: список дат
    - sums: список сумм
    - has_judgment: упоминание решения суда
    - has_writ: упоминание ИЛ
    - has_secured: упоминание залога/поручительства
    - base_contract: название/номер договора
    - court_case: номер арбитражного дела
    """
    text_lc = text.lower()

    has_judgment = any(
        kw in text_lc
        for kw in (
            "решение суда",
            "решением суда",
            "решением арбитражного суда",
            "решение арбитражного суда",
            "вступило в законную силу",
        )
    )
    has_writ = "исполнительный лист" in text_lc or "исполнительного листа" in text_lc
    has_secured = any(
        kw in text_lc for kw in ("залог", "поручительств", "банковская гарантия")
    )
    has_assignment_forbidden = any(
        kw in text_lc
        for kw in (
            "без согласия должника",
            "запрет уступки",
            "не подлежит уступке",
        )
    )
    has_counterclaim = "встречное требование" in text_lc or "встречный иск" in text_lc
    has_personal = "неразрывно связан с личн" in text_lc

    # Номер дела
    case_match = re.search(r"А\d{2}-\d{4,}/\d{4}", text)
    court_case = case_match.group(0) if case_match else None

    # Номер договора
    contract_match = re.search(
        r"договор[а-я\s]*?(?:№\s*|N\s*)?(\d+[/\\\-]?\d*[\.\d]*)",
        text,
        re.IGNORECASE,
    )
    base_contract = contract_match.group(0) if contract_match else None

    return {
        "inn": extract_inn_from_text(text),
        "ogrn": extract_ogrn_from_text(text),
        "dates": extract_dates(text),
        "sums": extract_sums(text),
        "has_judgment": has_judgment,
        "has_writ": has_writ,
        "has_secured": has_secured,
        "has_assignment_forbidden": has_assignment_forbidden,
        "has_counterclaim": has_counterclaim,
        "has_personal": has_personal,
        "court_case": court_case,
        "base_contract": base_contract,
    }


def extract_text(data: bytes, content_type: str = "") -> str:
    """Универсальное извлечение текста по content_type."""
    ct = content_type.lower()
    if "pdf" in ct or data[:4] == b"%PDF":
        return extract_text_from_pdf(data)
    if "word" in ct or "officedocument" in ct:
        return extract_text_from_docx(data)
    if "html" in ct or b"<html" in data[:200].lower():
        from selectolax.parser import HTMLParser

        tree = HTMLParser(data.decode("utf-8", errors="ignore"))
        return tree.text()
    if "text" in ct or "plain" in ct:
        return data.decode("utf-8", errors="ignore")
    # Попробуем PDF по сигнатуре
    if data[:4] == b"%PDF":
        return extract_text_from_pdf(data)
    return data.decode("utf-8", errors="ignore")
