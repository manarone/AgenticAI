from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})


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
    enable_rate_limiting: bool = Field(
        default=False,
        validation_alias="ENABLE_RATE_LIMITING",
    )
    telegram_webhook_rate_limit_requests: int = Field(
        default=60,
        validation_alias="TELEGRAM_WEBHOOK_RATE_LIMIT_REQUESTS",
        ge=1,
    )
    telegram_webhook_rate_limit_window_seconds: float = Field(
        default=60.0,
        validation_alias="TELEGRAM_WEBHOOK_RATE_LIMIT_WINDOW_SECONDS",
        gt=0,
    )
    task_create_rate_limit_requests: int = Field(
        default=30,
        validation_alias="TASK_CREATE_RATE_LIMIT_REQUESTS",
        ge=1,
    )
    task_create_rate_limit_window_seconds: float = Field(
        default=60.0,
        validation_alias="TASK_CREATE_RATE_LIMIT_WINDOW_SECONDS",
        gt=0,
    )
    task_api_jwt_secret: SecretStr | None = Field(
        default=None,
        validation_alias="TASK_API_JWT_SECRET",
    )
    task_api_jwt_audience: str = Field(
        default="agenticai-v1",
        validation_alias="TASK_API_JWT_AUDIENCE",
    )
    task_api_jwt_algorithm: str = Field(
        default="HS256",
        validation_alias="TASK_API_JWT_ALGORITHM",
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
    execution_runtime_backend: str = Field(
        default="docker",
        validation_alias="EXECUTION_RUNTIME_BACKEND",
    )
    execution_runtime_timeout_seconds: float = Field(
        default=300.0,
        validation_alias="EXECUTION_RUNTIME_TIMEOUT_SECONDS",
        gt=0,
    )
    execution_docker_image: str = Field(
        default="python:3.12-slim",
        validation_alias="EXECUTION_DOCKER_IMAGE",
    )
    execution_docker_memory_limit: str = Field(
        default="512m",
        validation_alias="EXECUTION_DOCKER_MEMORY_LIMIT",
    )
    execution_docker_nano_cpus: int = Field(
        default=500_000_000,
        validation_alias="EXECUTION_DOCKER_NANO_CPUS",
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

    @field_validator("task_api_jwt_algorithm", mode="before")
    @classmethod
    def normalize_task_api_jwt_algorithm(cls, value: str) -> str:
        """Normalize JWT algorithm for stable comparisons."""
        return str(value).upper()

    @field_validator("execution_runtime_backend", mode="before")
    @classmethod
    def normalize_execution_runtime_backend(cls, value: str) -> str:
        """Normalize runtime backend values for stable comparisons."""
        return str(value).lower()

    @model_validator(mode="after")
    def validate_backends(self) -> "Settings":
        """Validate backend compatibility for the current scaffold."""
        environment = self.environment.strip().lower()
        non_local_environment = environment not in LOCAL_ENVIRONMENTS
        database_url = self.database_url.get_secret_value().strip().lower()

        supported_backends = {"inmemory", "redis"}
        supported_execution_runtimes = {"noop", "docker"}
        supported_task_api_jwt_algorithms = {"HS256"}
        if self.bus_backend not in supported_backends:
            options = ", ".join(sorted(supported_backends))
            raise ValueError(f"BUS_BACKEND must be one of: {options}")
        if self.bus_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when BUS_BACKEND=redis")
        if self.task_api_jwt_algorithm not in supported_task_api_jwt_algorithms:
            options = ", ".join(sorted(supported_task_api_jwt_algorithms))
            raise ValueError(f"TASK_API_JWT_ALGORITHM must be one of: {options}")
        if not self.task_api_jwt_audience.strip():
            raise ValueError("TASK_API_JWT_AUDIENCE must not be blank")
        if self.execution_runtime_backend not in supported_execution_runtimes:
            options = ", ".join(sorted(supported_execution_runtimes))
            raise ValueError(f"EXECUTION_RUNTIME_BACKEND must be one of: {options}")
        if not self.execution_docker_image.strip():
            raise ValueError("EXECUTION_DOCKER_IMAGE must not be blank")
        if non_local_environment:
            if database_url.startswith("sqlite"):
                raise ValueError("DATABASE_URL must not use sqlite outside development/local/test")
            if self.telegram_webhook_secret is None and not self.allow_insecure_telegram_webhook:
                raise ValueError(
                    "TELEGRAM_WEBHOOK_SECRET is required outside development/local/test unless "
                    "ALLOW_INSECURE_TELEGRAM_WEBHOOK=true"
                )
            if self.task_api_jwt_secret is None:
                raise ValueError("TASK_API_JWT_SECRET is required outside development/local/test")

        return self


@lru_cache
def get_settings() -> Settings:
    """Build settings from environment variables."""
    return Settings()
