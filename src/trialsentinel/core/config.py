"""Typed, environment-driven configuration (12-factor)."""

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _package_version() -> str:
    try:
        return version("trialsentinel")
    except PackageNotFoundError:
        return "0.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TS_", extra="ignore")

    env: Literal["local", "dev", "staging", "prod"] = "local"
    log_level: str = "INFO"
    service_name: str = "trialsentinel-api"
    version: str = _package_version()

    postgres_dsn: SecretStr = SecretStr(
        "postgresql+asyncpg://trialsentinel:trialsentinel@localhost:5432/trialsentinel"
    )
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    aws_region: str = "us-east-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
