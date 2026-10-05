"""Typed, environment-driven configuration (12-factor)."""

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _package_version() -> str:
    try:
        return version("trialsentinel")
    except PackageNotFoundError:
        return "0.0.0"


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    return value.get_secret_value() or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TS_", extra="ignore")

    # --- service ---
    env: Literal["local", "dev", "staging", "prod"] = "local"
    log_level: str = "INFO"
    service_name: str = "trialsentinel-api"
    version: str = _package_version()

    # --- infrastructure ---
    postgres_dsn: SecretStr = SecretStr(
        "postgresql+asyncpg://trialsentinel:trialsentinel@localhost:5432/trialsentinel"
    )
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    aws_region: str = "us-east-1"

    # --- external sources: shared ---
    user_agent: str = "TrialSentinel/0.1 (+https://github.com/Saheed7/trialsentinel)"
    http_timeout_s: float = 30.0
    http_max_retries: int = 5

    # --- ClinicalTrials.gov ---
    ctgov_base_url: str = "https://clinicaltrials.gov/api/v2"
    ctgov_requests_per_second: float = 0.8

    # --- PubMed / NCBI E-utilities ---
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ncbi_tool: str = "trialsentinel"
    ncbi_email: str | None = None
    ncbi_api_key: SecretStr | None = None

    # --- openFDA ---
    openfda_base_url: str = "https://api.fda.gov"
    openfda_api_key: SecretStr | None = None
    openfda_requests_per_second: float = 3.5  # documented limit is 240/min (4/s)

    # --- storage ---
    raw_data_dir: Path = Path("data/raw")

    # --- derived values (methods stay inside the class: 4-space indent) ---
    @property
    def pubmed_requests_per_second(self) -> float:
        # NCBI allows 3 req/s without a key and 10 with one; stay below both.
        return 8.0 if _secret(self.ncbi_api_key) else 2.5

    def ncbi_params(self) -> dict[str, str]:
        params = {"tool": self.ncbi_tool}
        if self.ncbi_email:
            params["email"] = self.ncbi_email
        if key := _secret(self.ncbi_api_key):
            params["api_key"] = key
        return params

    def openfda_params(self) -> dict[str, str]:
        key = _secret(self.openfda_api_key)
        return {"api_key": key} if key else {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
