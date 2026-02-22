from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from agenticai.bus.inmemory import InMemoryBus
from agenticai.core.config import get_settings
from agenticai.db.base import Base
from agenticai.db.models import RuntimeSetting
from agenticai.db.runtime_settings import BUS_REDIS_FALLBACK_SETTING_KEY
from agenticai.db.session import build_engine
from agenticai.main import create_app


def test_startup_reads_runtime_bus_fallback_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """App startup should pass DB runtime override through to bus creation."""
    database_url = f"sqlite:///{tmp_path}/startup-settings.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BUS_REDIS_FALLBACK_TO_INMEMORY", "true")
    get_settings.cache_clear()

    engine = build_engine(database_url)
    Base.metadata.create_all(bind=engine)
    with Session(bind=engine) as session:
        session.add(
            RuntimeSetting(
                key=BUS_REDIS_FALLBACK_SETTING_KEY,
                value="false",
                description="test override",
            )
        )
        session.commit()
    engine.dispose()

    captured: dict[str, object] = {}

    def fake_create_bus(settings, *, redis_fallback_to_inmemory=None):
        captured["backend"] = settings.bus_backend
        captured["redis_fallback_to_inmemory"] = redis_fallback_to_inmemory
        return InMemoryBus()

    monkeypatch.setattr("agenticai.main.create_bus", fake_create_bus)

    with TestClient(create_app(start_coordinator=False)):
        pass

    assert captured["backend"] == "redis"
    assert captured["redis_fallback_to_inmemory"] is False
    get_settings.cache_clear()
