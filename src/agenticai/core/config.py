from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

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


def get_settings() -> Settings:
    """Build settings from environment variables."""
    return Settings()
