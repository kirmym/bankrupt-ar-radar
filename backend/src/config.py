"""Глобальные настройки приложения — pydantic-settings."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database (Railway отдаёт postgres:// — приводим к asyncpg-драйверу)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ar_radar"
    sync_database_url: str = ""

    # Single-process deploy: воркеры и бот живут в процессе API
    enable_workers: bool = True
    enable_bot: bool = True
    # Веб-статика (собранный Vite dist), раздаваемая FastAPI
    web_dist_dir: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""

    # ЕФРСБ REST
    efrsb_api_url: str = "https://bank-publications-demo.fedresurs.ru"
    efrsb_api_token: str = ""

    # ФССП
    fssp_api_url: str = "https://api-ip.fssprus.ru"
    fssp_api_token: str = ""

    # LLM
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0

    # Scoring defaults
    default_discount_a: float = 0.85
    default_discount_b: float = 0.65
    default_discount_c: float = 0.35
    default_discount_d: float = 0.05
    cost_court_rub: int = 150_000
    cost_enforcement_rub: int = 80_000
    cost_bankruptcy_rub: int = 300_000
    alternative_rate: float = 0.20

    # Intervals
    ingest_interval_minutes: int = 15
    enrich_interval_minutes: int = 60
    score_interval_minutes: int = 30
    alert_interval_minutes: int = 30

    # Efdup check
    efrsb_check_interval_minutes: int = 30

    @property
    def telegram_chat_ids_list(self) -> list[str]:
        return [x.strip() for x in self.telegram_chat_ids.split(",") if x.strip()]

    @property
    def async_database_url(self) -> str:
        """DATABASE_URL, гарантированно с asyncpg-драйвером.

        Railway/Heroku-стиль: postgres:// или postgresql:// → postgresql+asyncpg://
        """
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def docs_dir(self) -> Path:
        return Path(__file__).parent.parent.parent.parent / "docs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
