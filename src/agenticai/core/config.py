from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = Field(default="AgenticAI", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    bus_backend: str = Field(default="inmemory", alias="BUS_BACKEND")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    @model_validator(mode="after")
    def validate_backends(self) -> "Settings":
        backend = self.bus_backend.lower()
        if backend not in {"inmemory", "redis"}:
            raise ValueError("BUS_BACKEND must be one of: inmemory, redis")

        if backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when BUS_BACKEND=redis")

        self.bus_backend = backend
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
