from collections import Counter
from math import log2
from pathlib import Path
from typing import Literal
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def normalize_database_url(value: str) -> str:
    url = make_url(value)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def database_connect_args(database_url: str, ssl_mode: str | None, disable_prepared_statements: bool = False) -> dict:
    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        return {"check_same_thread": False}
    connect_args = {}
    if url.drivername.startswith("postgresql"):
        if ssl_mode:
            connect_args["sslmode"] = ssl_mode
        if disable_prepared_statements:
            connect_args["prepare_threshold"] = None
    return connect_args


def has_sufficient_secret_entropy(value: str) -> bool:
    if len(value) < 32 or len(set(value)) < 16:
        return False
    frequencies = Counter(value).values()
    entropy = -sum((count / len(value)) * log2(count / len(value)) for count in frequencies)
    return entropy >= 3.5


class Settings(BaseSettings):
    database_url: str = "sqlite:///./fintrack.db"
    database_ssl_mode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] | None = None
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
    database_disable_prepared_statements: bool = False
    secret_key: str = "development-only-change-me"
    frontend_origin: str = "http://localhost:5173"
    app_env: Literal["development", "test", "production"] = "development"
    rate_limit_storage_uri: str = Field(default="memory://", validation_alias=AliasChoices("RATE_LIMIT_STORAGE_URI", "REDIS_URL"))
    metrics_token: str | None = None
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / ".env", extra="ignore")

    @model_validator(mode="after")
    def require_production_secret(self):
        self.database_url = normalize_database_url(self.database_url)
        if self.app_env.lower() == "production":
            if self.secret_key == "development-only-change-me" or self.secret_key.startswith("replace-this-") or not has_sufficient_secret_entropy(self.secret_key):
                raise ValueError("Production requires a high-entropy SECRET_KEY; generate one with secrets.token_urlsafe(48)")
            if not self.frontend_origin.startswith("https://"):
                raise ValueError("Production FRONTEND_ORIGIN must use HTTPS")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL using psycopg")
            if self.database_ssl_mode != "verify-full":
                raise ValueError("Production requires DATABASE_SSL_MODE=verify-full")
            if not self.rate_limit_storage_uri.startswith("rediss://"):
                raise ValueError("Production requires TLS-protected Redis using a rediss:// URL")
            if not self.metrics_token or not has_sufficient_secret_entropy(self.metrics_token):
                raise ValueError("Production requires a high-entropy METRICS_TOKEN")
        return self


settings = Settings()
