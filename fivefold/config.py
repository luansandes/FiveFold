from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env.local", ".env"), extra="ignore")

    app_env: str = "development"
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./fivefold.db"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    google_maps_api_key: str | None = None
    admin_password_hash: str | None = None
    session_secret: str = "development-session-secret-change-me"
    cron_secret: str = "development-cron-secret-change-me"
    preview_signing_secret: str = "development-preview-secret-change-me"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_database_url(self) -> str:
        # Neon sometimes exposes the old postgres:// scheme.
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
