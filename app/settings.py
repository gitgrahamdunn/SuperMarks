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

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)


settings = Settings()
