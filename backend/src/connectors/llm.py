"""LLM-извлечение фактов из текста документов лота."""
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.config import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """\
Ты — юридический ассистент, извлекающий факты из документов по банкротству.

Твоя задача: прочитать текст документа и извлечь из него факты в строго-JSON-формате.
Верни ТОЛЬКО валидный JSON без пояснений.

Схема ответа:
{
  "debtor": {
    "name": "наименование ООО/ИП",
    "inn": "10 или 12 цифр",
    "ogrn": "13 или 15 цифр"
  },
  "claim": {
    "kind": "trade_ar" | "advance" | "loan" | "restitution" | "subsidiary" | "registry_claim_on_bankrupt" | "unknown",
    "principal": 0.0,  // тело долга в рублях
    "penalties": 0.0,  // пени/проценты
    "currency": "RUB",
    "base_contract": "номер договора",
    "base_date": "YYYY-MM-DD",
    "due_date": "YYYY-MM-DD",
    "court_case_no": "А40-12345/2023",
    "has_judgment": false,
    "has_writ": false,
    "secured": false,
    "assignment_forbidden": false,
    "counterclaim_risk": false,
    "personal_claim": false
  },
  "bankrupt": {
    "name": "наименование банкрота",
    "inn": "ИНН банкрота"
  }
}
"""


class LlmDebtorFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    inn: str | None = None
    ogrn: str | None = None

    @field_validator("inn")
    @classmethod
    def validate_inn(cls, value: str | None) -> str | None:
        if value is not None and (not value.isdigit() or len(value) not in (10, 12)):
            raise ValueError("invalid INN")
        return value


class LlmClaimFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str | None = Field(
        default=None,
        pattern="^(trade_ar|advance|loan|restitution|subsidiary|registry_claim_on_bankrupt|unknown)$",
    )
    principal: Decimal | None = None
    penalties: Decimal | None = None
    currency: str | None = None
    base_contract: str | None = None
    base_date: date | None = None
    due_date: date | None = None
    court_case_no: str | None = None
    has_judgment: bool | None = None
    has_writ: bool | None = None
    secured: bool | None = None
    assignment_forbidden: bool | None = None
    counterclaim_risk: bool | None = None
    personal_claim: bool | None = None


class LlmFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    debtor: LlmDebtorFacts | None = None
    claim: LlmClaimFacts | None = None
    bankrupt: LlmDebtorFacts | None = None


def validate_llm_facts(payload: Any) -> dict[str, Any] | None:
    """Validate and JSON-normalize an LLM response before storing it."""
    try:
        validated = LlmFacts.model_validate(payload)
    except ValidationError:
        return None
    return validated.model_dump(mode="json", exclude_none=True)


def build_extraction_prompt(text: str) -> list[dict]:
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Текст документа (первые 4000 символов):\n\n{text[:4000]}\n\nJSON:",
        },
    ]


async def extract_facts_with_llm(text: str, openai_client: Any = None) -> dict:
    """Извлекает факты через LLM. Если нет ключа — возвращает regex-факты."""
    if not openai_client:
        # fallback — regex
        from src.connectors.files import extract_facts_from_text

        return {"facts": extract_facts_from_text(text), "source": "regex"}

    try:
        messages = build_extraction_prompt(text)
        settings = get_settings()
        response = await openai_client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=settings.llm_temperature,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        facts = validate_llm_facts(json.loads(content or "{}"))
        if facts is None:
            raise ValueError("invalid LLM fact schema")
        return {"facts": facts, "source": "llm"}
    except Exception as e:
        logger.exception("LLM extraction failed: %s", e)
        from src.connectors.files import extract_facts_from_text

        return {"facts": extract_facts_from_text(text), "source": "regex_fallback"}
