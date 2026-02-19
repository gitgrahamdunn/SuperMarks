"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the SuperMarks backend."""

    model_config = SettingsConfigDict(env_prefix="SUPERMARKS_", extra="ignore")

    app_name: str = "SuperMarks API"
    sqlite_path: str = "./data/supermarks.db"
    data_dir: str = "./data"
    max_upload_mb: int = 25

    # Deployment toggles
    vercel_environment: bool = False

    # CORS configuration
    cors_origins: str = (
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )
    cors_allow_origin_regex: str = r"https://.*\.vercel\.app"

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

if settings.vercel_environment:
    # Vercel serverless functions run on a read-only filesystem except /tmp.
    # Default to /tmp when deployment toggles are enabled.
    settings.data_dir = "/tmp/supermarks-data"
    settings.sqlite_path = "/tmp/supermarks-data/supermarks.db"
