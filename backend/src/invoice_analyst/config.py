"""Settings, read from the environment and the project-root .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # extra="ignore": the shared .env also holds values meant for other tools, which the backend must not use
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://invoice:invoice@localhost:5433/invoice_analyst"

    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    image_model: str = "meta/muse-glimmer-30b"
    text_model: str = "z-ai/glm-5.3"
    llm_rpm: int = 30
    llm_max_in_flight: int = 4

    storage_dir: Path = ROOT / "data" / "storage"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
