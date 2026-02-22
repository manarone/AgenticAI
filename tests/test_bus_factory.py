from pytest import MonkeyPatch

from agenticai.bus.factory import create_bus
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
    assert isinstance(create_bus(settings), InMemoryBus)


def test_settings_allow_redis_when_url_is_provided(monkeypatch: MonkeyPatch) -> None:
    """Redis backend can be selected when REDIS_URL is configured."""
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
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
    try:
        Settings()
    except ValueError as exc:
        assert "REDIS_URL is required when BUS_BACKEND=redis" in str(exc)
    else:
        raise AssertionError("Expected redis URL validation failure")
