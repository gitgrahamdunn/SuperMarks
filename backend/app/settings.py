"""Application settings loaded from environment variables."""

import os
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_DATA_DIR = BASE_DIR / "data"
DEFAULT_VERCEL_DATA_DIR = Path("/tmp/supermarks")


TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in TRUTHY_VALUES)


def _running_on_vercel() -> bool:
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV") or _is_truthy(os.getenv("SUPERMARKS_VERCEL_ENVIRONMENT")))


def _default_data_dir() -> str:
    if _running_on_vercel():
        return str(DEFAULT_VERCEL_DATA_DIR)
    return str(DEFAULT_LOCAL_DATA_DIR)


class Settings(BaseSettings):
    """Runtime configuration for the SuperMarks backend."""

    model_config = SettingsConfigDict(env_prefix="SUPERMARKS_", extra="ignore")

    app_name: str = "SuperMarks API"
    data_dir: str = Field(
        default_factory=_default_data_dir,
        validation_alias=AliasChoices("SUPERMARKS_DATA_DIR", "DATA_DIR"),
    )
    sqlite_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_SQLITE_PATH", "SQLITE_PATH"),
    )
    max_upload_mb: int = 25

    # Deployment toggles
    vercel_environment: bool = False

    # CORS configuration
    cors_allow_origins: str = Field(
        default="*",
        validation_alias=AliasChoices("SUPERMARKS_CORS_ALLOW_ORIGINS", "CORS_ALLOW_ORIGINS"),
    )

    @model_validator(mode="after")
    def _set_sqlite_path(self) -> "Settings":
        if not self.sqlite_path:
            self.sqlite_path = str(Path(self.data_dir) / "supermarks.db")
        return self

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_allow_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


settings = Settings()
