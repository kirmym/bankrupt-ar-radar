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


def _has_positive_mention(text: str, patterns: tuple[str, ...]) -> bool:
    """Find a fact while ignoring simple negations immediately before it."""
    for pattern in patterns:
        for match in re.finditer(re.escape(pattern), text):
            prefix = text[max(0, match.start() - 45) : match.start()]
            suffix = text[match.end() : match.end() + 35]
            if re.search(r"(?:нет|не|без|отсутств\w*|не имеется)\s*$", prefix):
                continue
            if re.match(r"\s*(?:нет|не\b|отсутств\w*)", suffix):
                continue
            return True
    return False


def extract_text_from_pdf(data: bytes) -> str:
    """Извлекает текст из PDF. Использует pdfplumber, иначе pypdf."""
    try:
        import pypdf  # type: ignore[import-not-found]

        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    try:
        import pdfplumber  # type: ignore[import-not-found]

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass
    return ""


def extract_text_from_docx(data: bytes) -> str:
    """Извлекает текст из DOCX."""
    try:
        from docx import Document  # type: ignore[import-not-found]

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

    has_judgment = _has_positive_mention(
        text_lc,
        (
            "решение суда",
            "решением суда",
            "решением арбитражного суда",
            "решение арбитражного суда",
            "вступило в законную силу",
        ),
    )
    has_writ = _has_positive_mention(
        text_lc, ("исполнительный лист", "исполнительного листа")
    )
    has_secured = _has_positive_mention(
        text_lc, ("залог", "поручительств", "банковская гарантия")
    )
    has_assignment_forbidden = (
        "без согласия должника" in text_lc
        or _has_positive_mention(text_lc, ("запрет уступки", "не подлежит уступке"))
    ) and "уступка разрешена" not in text_lc
    has_counterclaim = _has_positive_mention(
        text_lc, ("встречное требование", "встречный иск")
    )
    has_personal = _has_positive_mention(text_lc, ("неразрывно связан с личн",))

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


def propose_fact_updates(
    extracted: dict,
    claim: object | None = None,
    debtor: object | None = None,
) -> dict[str, object]:
    """Build a reviewable fact proposal without mutating ORM entities."""
    facts = extracted.get("facts", extracted)
    claim_facts = facts.get("claim") or {}
    debtor_facts = facts.get("debtor") or {}
    claim_fields = (
        "kind",
        "principal",
        "penalties",
        "currency",
        "base_contract",
        "base_date",
        "due_date",
        "court_case_no",
        "has_judgment",
        "has_writ",
        "secured",
        "assignment_forbidden",
        "counterclaim_risk",
        "personal_claim",
    )
    debtor_fields = ("name", "inn", "ogrn")

    updates: dict[str, dict[str, object]] = {"claim": {}, "debtor": {}}
    conflicts: list[str] = []
    for field in claim_fields:
        value = claim_facts.get(field)
        if value is None:
            continue
        current = getattr(claim, field, None) if claim is not None else None
        if current is None:
            updates["claim"][field] = value
        elif str(current) != str(value):
            conflicts.append(f"claim.{field}")
    for field in debtor_fields:
        value = debtor_facts.get(field)
        if value is None:
            continue
        current = getattr(debtor, field, None) if debtor is not None else None
        if current is None:
            updates["debtor"][field] = value
        elif str(current) != str(value):
            conflicts.append(f"debtor.{field}")

    return {
        "updates": updates,
        "conflicts": sorted(conflicts),
        "requires_review": bool(conflicts),
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
