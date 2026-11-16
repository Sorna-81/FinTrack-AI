from typing import Literal
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./fintrack.db"
    secret_key: str = "development-only-change-me"
    frontend_origin: str = "http://localhost:5173"
    app_env: Literal["development", "test", "production"] = "development"
    rate_limit_storage_uri: str = Field(default="memory://", validation_alias=AliasChoices("RATE_LIMIT_STORAGE_URI", "REDIS_URL"))
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def require_production_secret(self):
        if self.app_env.lower() == "production":
            if self.secret_key == "development-only-change-me" or self.secret_key.startswith("replace-this-") or len(self.secret_key) < 32:
                raise ValueError("Production requires SECRET_KEY with at least 32 characters")
            if not self.frontend_origin.startswith("https://"):
                raise ValueError("Production FRONTEND_ORIGIN must use HTTPS")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL using psycopg")
            if not self.rate_limit_storage_uri.startswith(("redis://", "rediss://")):
                raise ValueError("Production requires REDIS_URL for shared API rate limits")
        return self


settings = Settings()
