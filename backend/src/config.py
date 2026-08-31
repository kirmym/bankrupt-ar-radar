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
    # The API may host the scheduler in small deployments.  A PostgreSQL
    # advisory lock guarantees that only one instance becomes the leader.
    enable_workers: bool = True
    worker_leader_lock_key: int = 842_917_331
    migration_lock_key: int = 842_917_332
    # Веб-статика (собранный Vite dist), раздаваемая FastAPI
    web_dist_dir: str = ""

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"
    # Если задан, защищает диагностические и изменяющие API по X-API-Key.
    # Пустое значение оставляет локальную разработку совместимой со старым режимом.
    api_auth_token: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""

    efrsb_public_url: str = "https://old.bankrot.fedresurs.ru"
    # Operational seed sources. CDT is enabled by default because its public
    # JSON endpoints are currently reachable without credentials. EFRSB can be
    # added back with INGEST_SOURCES=cdt,efrsb when its public route works.
    ingest_sources: str = "cdt"
    cdt_api_url: str = "https://webapi.torgi.cdtrf.ru"
    cdt_ingest_max_items: int = 250
    cdt_detail_concurrency: int = 4
    # Official FNS EGRUL extract is free but slower than the search card.
    # Keep it enabled for risk flags, with a bounded polling window.
    egrul_extract_enabled: bool = True
    egrul_extract_timeout_seconds: int = 30
    egrul_extract_poll_seconds: float = 0.5
    egrul_extract_max_polls: int = 8
    # CDP endpoint already opened in CloakBrowser; optional challenge fallback.
    cloakbrowser_cdp_url: str = ""
    cloakbrowser_timeout_seconds: int = 90
    cloakbrowser_wait_seconds: int = 8
    ingest_max_pages: int = 10
    ingest_page_size: int = 50

    # ФССП
    fssp_api_url: str = "https://api-ip.fssprus.ru"
    fssp_api_token: str = ""
    # Только источники с подтверждённым бесплатным API. Пусто = API выключены.
    free_api_sources: str = ""

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
    etp_interval_minutes: int = 15
    score_interval_minutes: int = 30
    alert_interval_minutes: int = 30
    enrich_max_attempts: int = 8
    etp_max_attempts: int = 8
    document_max_attempts: int = 8
    price_freshness_hours: int = 24

    # Data retention is deliberately disabled by default.  Set a positive
    # number after choosing an archival policy for a production deployment.
    retention_days: int = 0
    score_snapshot_retention_days: int = 180
    alert_retention_days: int = 180
    import_run_retention_days: int = 180

    @property
    def telegram_chat_ids_list(self) -> list[str]:
        return [x.strip() for x in self.telegram_chat_ids.split(",") if x.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def free_api_sources_list(self) -> set[str]:
        return {x.strip().lower() for x in self.free_api_sources.split(",") if x.strip()}

    @property
    def ingest_sources_list(self) -> list[str]:
        return [x.strip().lower() for x in self.ingest_sources.split(",") if x.strip()]

    @property
    def primary_ingest_source(self) -> str:
        source = self.ingest_sources_list[0] if self.ingest_sources_list else "cdt"
        return {"cdt": "cdt_public", "efrsb": "efrsb_public"}.get(source, source)

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
