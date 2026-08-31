"""Конфигурация бота."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""
    telegram_allowed_user_ids: str = ""
    bot_public: bool = False
    api_base_url: str = "http://backend:8000"
    api_auth_token: str = ""

    @property
    def chat_ids_list(self) -> list[str]:
        return [x.strip() for x in self.telegram_chat_ids.split(",") if x.strip()]

    @property
    def telegram_allowed_user_ids_list(self) -> set[int]:
        return {
            int(value.strip())
            for value in self.telegram_allowed_user_ids.split(",")
            if value.strip().lstrip("-").isdigit()
        }


settings = BotSettings()
