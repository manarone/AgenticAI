import pytest
from pytest import MonkeyPatch

from agenticai.bus.factory import create_bus
from agenticai.bus.failover import RedisFailoverBus
from agenticai.bus.inmemory import InMemoryBus
from agenticai.bus.redis import RedisBus
from agenticai.core.config import Settings, get_settings


def test_settings_default_to_inmemory(monkeypatch: MonkeyPatch) -> None:
    """Local/dev setup should continue defaulting to in-memory queue backend."""
    monkeypatch.delenv("BUS_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    get_settings.cache_clear()
    settings = Settings()
    assert settings.bus_backend == "inmemory"
    assert settings.bus_redis_fallback_to_inmemory is False
    assert isinstance(create_bus(settings), InMemoryBus)


def test_settings_allow_redis_when_url_is_provided(monkeypatch: MonkeyPatch) -> None:
    """Redis backend can be selected when REDIS_URL is configured."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BUS_REDIS_FALLBACK_TO_INMEMORY", "false")
    get_settings.cache_clear()
    settings = Settings()
    bus = create_bus(settings)
    assert settings.bus_backend == "redis"
    assert isinstance(bus, RedisBus)


def test_settings_require_redis_url(monkeypatch: MonkeyPatch) -> None:
    """Selecting Redis without a URL is rejected at settings validation time."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.delenv("REDIS_URL", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError) as excinfo:
        Settings()
    assert "REDIS_URL is required when BUS_BACKEND=redis" in str(excinfo.value)


def test_create_bus_falls_back_to_inmemory_when_redis_ping_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    """Redis startup health failures should fall back to in-memory when enabled."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BUS_REDIS_FALLBACK_TO_INMEMORY", "true")
    monkeypatch.setattr(RedisBus, "ping", lambda self: False)
    get_settings.cache_clear()

    settings = Settings()
    bus = create_bus(settings)

    assert isinstance(bus, InMemoryBus)


def test_create_bus_keeps_redis_when_fallback_disabled(monkeypatch: MonkeyPatch) -> None:
    """Fallback can be disabled to keep strict Redis behavior."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BUS_REDIS_FALLBACK_TO_INMEMORY", "false")
    monkeypatch.setattr(RedisBus, "ping", lambda self: False)
    get_settings.cache_clear()

    settings = Settings()
    bus = create_bus(settings)

    assert isinstance(bus, RedisBus)


def test_create_bus_uses_failover_wrapper_when_redis_is_healthy(monkeypatch: MonkeyPatch) -> None:
    """Healthy Redis with fallback enabled should still be wrapped for runtime failover."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BUS_REDIS_FALLBACK_TO_INMEMORY", "true")
    monkeypatch.setattr(RedisBus, "ping", lambda self: True)
    get_settings.cache_clear()

    settings = Settings()
    bus = create_bus(settings)

    assert isinstance(bus, RedisFailoverBus)


def test_create_bus_runtime_failover_switches_to_inmemory(monkeypatch: MonkeyPatch) -> None:
    """Runtime Redis failures should switch to in-memory and continue serving requests."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BUS_REDIS_FALLBACK_TO_INMEMORY", "true")
    monkeypatch.setattr(RedisBus, "ping", lambda self: True)
    get_settings.cache_clear()

    redis_enqueue_calls = {"count": 0}

    def broken_enqueue(
        self,
        queue: str,
        job_id: str,
        payload: dict[str, object],
    ) -> bool:
        redis_enqueue_calls["count"] += 1
        raise RuntimeError("redis offline")

    monkeypatch.setattr(RedisBus, "enqueue", broken_enqueue)

    settings = Settings()
    bus = create_bus(settings)

    assert isinstance(bus, RedisFailoverBus)
    assert bus.enqueue("tasks", "job-1", {"task_id": "job-1"}) is True
    assert redis_enqueue_calls["count"] == 1
    assert bus.enqueue("tasks", "job-1", {"task_id": "job-1"}) is False
    assert redis_enqueue_calls["count"] == 1


def test_settings_require_webhook_secret_in_production(monkeypatch: MonkeyPatch) -> None:
    """Production environment should fail closed when webhook secret is absent."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", raising=False)
    monkeypatch.setenv("TASK_API_AUTH_TOKEN", "token")
    get_settings.cache_clear()
    with pytest.raises(ValueError) as excinfo:
        Settings()
    assert "TELEGRAM_WEBHOOK_SECRET is required" in str(excinfo.value)


def test_settings_allow_insecure_webhook_override_in_production(monkeypatch: MonkeyPatch) -> None:
    """Production can explicitly opt into insecure webhook mode when required."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", "true")
    monkeypatch.setenv("TASK_API_AUTH_TOKEN", "token")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.allow_insecure_telegram_webhook is True


def test_settings_require_task_api_token_in_production(monkeypatch: MonkeyPatch) -> None:
    """Production environment should fail closed when task API token is absent."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.delenv("TASK_API_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_TASK_API", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError) as excinfo:
        Settings()
    assert "TASK_API_AUTH_TOKEN is required" in str(excinfo.value)


def test_settings_allow_insecure_task_api_override_in_production(monkeypatch: MonkeyPatch) -> None:
    """Production can explicitly opt into insecure task API mode when required."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.delenv("TASK_API_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_TASK_API", "true")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.allow_insecure_task_api is True
