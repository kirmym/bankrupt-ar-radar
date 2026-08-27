"""LLM-извлечение фактов из текста документов лота."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

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
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return {"facts": json.loads(content), "source": "llm"}
    except Exception as e:
        logger.exception("LLM extraction failed: %s", e)
        from src.connectors.files import extract_facts_from_text

        return {"facts": extract_facts_from_text(text), "source": "regex_fallback"}
