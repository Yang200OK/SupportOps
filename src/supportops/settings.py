"""数据库配置保持显式；空值代表未配置，不选择备用存储。"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPPORTOPS_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
    database_url: SecretStr | None = None
    session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)

    @field_validator("database_url", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value):
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def only_explicit_postgresql(cls, value):
        if value is not None:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+psycopg" or url.database not in (
                "supportops",
                "supportops_test",
            ):
                raise ValueError("仅允许本项目显式的 PostgreSQL 配置。")
        return value
