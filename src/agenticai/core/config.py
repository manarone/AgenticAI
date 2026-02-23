from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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
    host: str = Field(default="127.0.0.1", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    bus_backend: str = Field(default="inmemory", validation_alias="BUS_BACKEND")
    bus_redis_fallback_to_inmemory: bool = Field(
        default=False,
        validation_alias="BUS_REDIS_FALLBACK_TO_INMEMORY",
    )
    task_api_auth_token: SecretStr | None = Field(
        default=None,
        validation_alias="TASK_API_AUTH_TOKEN",
    )
    task_api_actor_hmac_secret: SecretStr | None = Field(
        default=None,
        validation_alias="TASK_API_ACTOR_HMAC_SECRET",
    )
    allow_insecure_task_api: bool = Field(
        default=False,
        validation_alias="ALLOW_INSECURE_TASK_API",
    )
    allow_insecure_telegram_webhook: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ALLOW_INSECURE_TELEGRAM_WEBHOOK",
            "ALLOW_UNAUTHENTICATED_TELEGRAM_WEBHOOK",
            "ALLOW_INSECURE_WEBHOOK",
        ),
    )
    coordinator_poll_interval_seconds: float = Field(
        default=0.1,
        validation_alias="COORDINATOR_POLL_INTERVAL_SECONDS",
        gt=0,
    )
    coordinator_batch_size: int = Field(
        default=10,
        validation_alias="COORDINATOR_BATCH_SIZE",
        ge=1,
    )
    task_recovery_scan_interval_seconds: float = Field(
        default=30.0,
        validation_alias="TASK_RECOVERY_SCAN_INTERVAL_SECONDS",
        gt=0,
    )
    task_recovery_batch_size: int = Field(
        default=100,
        validation_alias="TASK_RECOVERY_BATCH_SIZE",
        ge=1,
    )
    task_recovery_queued_age_seconds: float = Field(
        default=30.0,
        validation_alias="TASK_RECOVERY_QUEUED_AGE_SECONDS",
        gt=0,
    )
    task_recovery_running_timeout_seconds: float = Field(
        default=1800.0,
        validation_alias="TASK_RECOVERY_RUNNING_TIMEOUT_SECONDS",
        gt=0,
    )
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    telegram_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias="TELEGRAM_WEBHOOK_SECRET",
    )
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
        environment = self.environment.strip().lower()
        non_local_environment = environment not in {"development", "dev", "local", "test"}
        database_url = self.database_url.get_secret_value().strip().lower()

        supported_backends = {"inmemory", "redis"}
        if self.bus_backend not in supported_backends:
            options = ", ".join(sorted(supported_backends))
            raise ValueError(f"BUS_BACKEND must be one of: {options}")
        if self.bus_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when BUS_BACKEND=redis")
        if non_local_environment:
            if database_url.startswith("sqlite"):
                raise ValueError("DATABASE_URL must not use sqlite outside development/local/test")
            if self.telegram_webhook_secret is None and not self.allow_insecure_telegram_webhook:
                raise ValueError(
                    "TELEGRAM_WEBHOOK_SECRET is required outside development/local/test unless "
                    "ALLOW_INSECURE_TELEGRAM_WEBHOOK=true"
                )
            if self.task_api_auth_token is None and not self.allow_insecure_task_api:
                raise ValueError(
                    "TASK_API_AUTH_TOKEN is required outside development/local/test unless "
                    "ALLOW_INSECURE_TASK_API=true"
                )
            if self.task_api_auth_token is not None and self.task_api_actor_hmac_secret is None:
                raise ValueError(
                    "TASK_API_ACTOR_HMAC_SECRET is required outside development/local/test when "
                    "TASK_API_AUTH_TOKEN is configured"
                )

        return self


@lru_cache
def get_settings() -> Settings:
    """Build settings from environment variables."""
    return Settings()
