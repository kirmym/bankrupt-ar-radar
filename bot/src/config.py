"""Конфигурация бота."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""
    api_base_url: str = "http://backend:8000"

    @property
    def chat_ids_list(self) -> list[str]:
        return [x.strip() for x in self.telegram_chat_ids.split(",") if x.strip()]


settings = BotSettings()
