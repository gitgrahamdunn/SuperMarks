"""Application settings loaded from environment variables."""

import os
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_DATA_DIR = BASE_DIR / "data"
DEFAULT_EPHEMERAL_DATA_DIR = Path("/tmp/supermarks")
DEFAULT_FRONTEND_DIST_DIR = BASE_DIR.parent / "frontend" / "dist"


TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in TRUTHY_VALUES)


def _running_in_managed_runtime() -> bool:
    return bool(
        _is_truthy(os.getenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT"))
        or _is_truthy(os.getenv("MANAGED_RUNTIME_ENVIRONMENT"))
    )


def _default_data_dir() -> str:
    if _running_in_managed_runtime():
        return str(DEFAULT_EPHEMERAL_DATA_DIR)
    return str(DEFAULT_LOCAL_DATA_DIR)


def _normalize_database_url(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("postgres://"):
        return f"postgresql+psycopg://{normalized[len('postgres://') :]}"
    if normalized.startswith("postgresql://"):
        return f"postgresql+psycopg://{normalized[len('postgresql://') :]}"
    return normalized


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
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_DATABASE_URL", "DATABASE_URL"),
    )
    d1_bridge_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_D1_BRIDGE_URL", "D1_BRIDGE_URL"),
    )
    d1_bridge_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_D1_BRIDGE_TOKEN", "D1_BRIDGE_TOKEN", "BACKEND_API_KEY"),
    )
    d1_bridge_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("SUPERMARKS_D1_BRIDGE_TIMEOUT_SECONDS", "D1_BRIDGE_TIMEOUT_SECONDS"),
    )
    allow_production_sqlite: bool = Field(
        default=False,
        validation_alias=AliasChoices("SUPERMARKS_ALLOW_PRODUCTION_SQLITE", "ALLOW_PRODUCTION_SQLITE"),
    )
    max_upload_mb: int = 25
    storage_backend: str = Field(
        default="local",
        validation_alias=AliasChoices("SUPERMARKS_STORAGE_BACKEND", "STORAGE_BACKEND"),
    )
    s3_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_S3_ENDPOINT_URL", "S3_ENDPOINT_URL"),
    )
    s3_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_S3_BUCKET", "S3_BUCKET"),
    )
    s3_access_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID"),
    )
    s3_secret_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_S3_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"),
    )
    s3_region: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_S3_REGION", "S3_REGION"),
    )
    s3_public_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPERMARKS_S3_PUBLIC_BASE_URL", "S3_PUBLIC_BASE_URL"),
    )
    blob_public_access: str = Field(
        default="public",
        validation_alias=AliasChoices("SUPERMARKS_BLOB_PUBLIC_ACCESS", "BLOB_PUBLIC_ACCESS"),
    )
    serve_frontend: bool = Field(
        default=False,
        validation_alias=AliasChoices("SUPERMARKS_SERVE_FRONTEND", "SERVE_FRONTEND"),
    )
    frontend_dist_dir: str = Field(
        default=str(DEFAULT_FRONTEND_DIST_DIR),
        validation_alias=AliasChoices("SUPERMARKS_FRONTEND_DIST_DIR", "FRONTEND_DIST_DIR"),
    )

    # Deployment toggles
    managed_runtime_environment: bool = Field(
        default_factory=lambda: _running_in_managed_runtime(),
        validation_alias=AliasChoices(
            "SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT",
            "MANAGED_RUNTIME_ENVIRONMENT",
        ),
    )

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
    def is_production(self) -> bool:
        env = os.getenv("SUPERMARKS_ENV", os.getenv("ENV", "")).strip().lower()
        return self.managed_runtime_environment or env in {"prod", "production"}

    @property
    def repository_backend(self) -> str:
        return (os.getenv("SUPERMARKS_REPOSITORY_BACKEND", "sqlmodel") or "sqlmodel").strip().lower()

    @property
    def hosted_d1_bridge_enabled(self) -> bool:
        return self.managed_runtime_environment and self.repository_backend == "d1-bridge"

    @property
    def effective_database_url(self) -> str:
        if self.hosted_d1_bridge_enabled:
            raise RuntimeError(
                "DATABASE_URL is not used when SUPERMARKS_REPOSITORY_BACKEND=d1-bridge on the managed runtime."
            )
        if self.is_production:
            if self.database_url and self.database_url.strip():
                return _normalize_database_url(self.database_url)
            if self.allow_production_sqlite and not self.managed_runtime_environment:
                return self.sqlite_url
            raise RuntimeError(
                "DATABASE_URL is required in production unless SUPERMARKS_ALLOW_PRODUCTION_SQLITE=1 is set for self-hosting."
            )
        if self.database_url and self.database_url.strip():
            return _normalize_database_url(self.database_url)
        return self.sqlite_url

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_allow_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def has_d1_bridge(self) -> bool:
        return bool((self.d1_bridge_url or "").strip() and (self.d1_bridge_token or "").strip())


settings = Settings()
