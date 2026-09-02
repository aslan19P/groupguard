from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    bootstrap_token: SecretStr = Field(min_length=16, max_length=54)
    database_url: str
    phone_hmac_secret: SecretStr = Field(min_length=16)
    timezone: str = "Asia/Tashkent"
    log_level: str = "INFO"
    similarity_threshold: int = Field(default=85, ge=50, le=100)
    retention_interval_seconds: int = Field(default=3600, ge=60)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_database_url() -> str:
    return DatabaseSettings().database_url
