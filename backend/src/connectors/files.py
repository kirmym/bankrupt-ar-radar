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

OCR_MAX_PAGES = 20
OCR_DPI = 200


def extract_text_with_ocr(data: bytes, content_type: str = "") -> str:
    """OCR image-only PDFs when the optional runtime tools are available."""
    ct = content_type.lower()
    if "pdf" not in ct and data[:4] != b"%PDF":
        return ""
    try:
        import pytesseract  # type: ignore[import-untyped]
        from pdf2image import convert_from_bytes  # type: ignore[import-not-found]
    except ImportError:
        logger.info("OCR dependencies are not installed")
        return ""
    try:
        pages = convert_from_bytes(
            data,
            dpi=OCR_DPI,
            first_page=1,
            last_page=OCR_MAX_PAGES,
            thread_count=1,
        )
        return "\n".join(
            pytesseract.image_to_string(page, lang="rus+eng", config="--psm 6")
            for page in pages
        ).strip()
    except Exception as exc:
        logger.warning("OCR could not parse document: %s", type(exc).__name__)
        return ""


def _mention_state(
    text: str, patterns: tuple[str, ...]
) -> tuple[bool | None, str | None]:
    """Return ``true``, explicit ``false`` or ``unknown`` plus evidence.

    A missing phrase is not evidence of a negative legal fact.  Only a nearby
    explicit negation produces ``false``; all other absence remains unknown.
    """
    negative_snippet: str | None = None
    for pattern in patterns:
        for match in re.finditer(re.escape(pattern), text):
            prefix = text[max(0, match.start() - 45) : match.start()]
            suffix = text[match.end() : match.end() + 35]
            snippet = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)].strip()
            if re.search(r"(?:нет|не|без|отсутств\w*|не имеется)\s*$", prefix):
                negative_snippet = negative_snippet or snippet
                continue
            if re.match(r"\s*(?:нет|не\b|отсутств\w*)", suffix):
                negative_snippet = negative_snippet or snippet
                continue
            return True, snippet
    if negative_snippet:
        return False, negative_snippet
    return None, None


def _has_positive_mention(text: str, patterns: tuple[str, ...]) -> bool:
    """Backward-compatible boolean helper for callers outside the parser."""
    return _mention_state(text, patterns)[0] is True


def extract_text_from_pdf(data: bytes) -> str:
    """Извлекает текст из PDF. Использует pdfplumber, иначе pypdf."""
    try:
        import pypdf  # type: ignore[import-not-found]

        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pypdf could not parse document: %s", type(exc).__name__)
    try:
        import pdfplumber  # type: ignore[import-not-found]

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pdfplumber could not parse document: %s", type(exc).__name__)
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

    has_judgment, judgment_evidence = _mention_state(
        text_lc,
        (
            "решение суда",
            "решения суда",
            "решением суда",
            "решением арбитражного суда",
            "решение арбитражного суда",
            "вступило в законную силу",
        ),
    )
    has_writ, writ_evidence = _mention_state(
        text_lc, ("исполнительный лист", "исполнительного листа")
    )
    has_secured, secured_evidence = _mention_state(
        text_lc, ("залог", "поручительств", "банковская гарантия")
    )
    assignment_evidence: str | None = None
    has_assignment_forbidden: bool | None
    if "уступка разрешена" in text_lc:
        has_assignment_forbidden = False
        start = text_lc.index("уступка разрешена")
        assignment_evidence = text[max(0, start - 80) : start + 100].strip()
    elif "без согласия должника" in text_lc:
        has_assignment_forbidden = True
        start = text_lc.index("без согласия должника")
        assignment_evidence = text[max(0, start - 80) : start + 100].strip()
    else:
        assignment_state, assignment_evidence = _mention_state(
            text_lc,
            (
                "без согласия должника",
                "запрет уступки",
                "не подлежит уступке",
                "уступка запрещена",
                "уступка не разрешена",
            ),
        )
        has_assignment_forbidden = assignment_state
    has_counterclaim, counterclaim_evidence = _mention_state(
        text_lc, ("встречное требование", "встречный иск")
    )
    has_personal, personal_evidence = _mention_state(text_lc, ("неразрывно связан с личн",))

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

    evidence = {
        key: {"snippet": value, "page": None}
        for key, value in {
            "has_judgment": judgment_evidence,
            "has_writ": writ_evidence,
            "has_secured": secured_evidence,
            "has_assignment_forbidden": assignment_evidence,
            "has_counterclaim": counterclaim_evidence,
            "has_personal": personal_evidence,
        }.items()
        if value
    }
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
        "evidence": evidence,
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
    if not claim_facts and not debtor_facts:
        # Regex extraction returns a flat payload while the LLM contract uses
        # claim/debtor namespaces.  Normalize both forms before generating a
        # reviewable proposal; otherwise the fallback silently produced an
        # empty proposal for every document.
        claim_facts = {
            field: facts.get(source)
            for field, source in {
                "has_judgment": "has_judgment",
                "has_writ": "has_writ",
                "secured": "has_secured",
                "assignment_forbidden": "has_assignment_forbidden",
                "counterclaim_risk": "has_counterclaim",
                "personal_claim": "has_personal",
                "court_case_no": "court_case",
                "base_contract": "base_contract",
            }.items()
            if facts.get(source) is not None
        }
        inns = facts.get("inn") or []
        ogrns = facts.get("ogrn") or []
        if inns:
            debtor_facts["inn"] = inns[0]
        if ogrns:
            debtor_facts["ogrn"] = ogrns[0]
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

    evidence_map = facts.get("evidence") if isinstance(facts.get("evidence"), dict) else {}
    proposal_evidence: dict[str, object] = {}
    evidence_aliases = {
        "has_judgment": "has_judgment",
        "has_writ": "has_writ",
        "secured": "has_secured",
        "assignment_forbidden": "has_assignment_forbidden",
        "counterclaim_risk": "has_counterclaim",
        "personal_claim": "has_personal",
        "court_case_no": "court_case",
        "base_contract": "base_contract",
    }
    for field in updates["claim"]:
        source_field = evidence_aliases.get(field, field)
        if source_field in evidence_map:
            proposal_evidence[field] = evidence_map[source_field]
    if "inn" in updates["debtor"] and "inn" in evidence_map:
        proposal_evidence["debtor.inn"] = evidence_map["inn"]

    return {
        "updates": updates,
        "conflicts": sorted(conflicts),
        "requires_review": bool(conflicts),
        "evidence": proposal_evidence,
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
