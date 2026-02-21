from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_name: str = Field(default="AgenticAI", validation_alias="APP_NAME")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    bus_backend: str = Field(default="inmemory", validation_alias="BUS_BACKEND")
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    database_url: SecretStr = Field(
        default="sqlite:///./agenticai.db",
        validation_alias="DATABASE_URL",
    )

    @field_validator("bus_backend", mode="before")
    @classmethod
    def normalize_bus_backend(cls, value: str) -> str:
        """Normalize BUS_BACKEND to lowercase for stable comparisons."""
        return str(value).lower()

    @model_validator(mode="after")
    def validate_backends(self) -> "Settings":
        """Validate backend compatibility for the current scaffold."""
        if self.bus_backend != "inmemory":
            raise ValueError(
                "BUS_BACKEND must be 'inmemory' for now; redis backend is not implemented yet"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Build settings from environment variables."""
    return Settings()
