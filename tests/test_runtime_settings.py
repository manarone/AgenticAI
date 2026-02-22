from pathlib import Path

from sqlalchemy.orm import Session

from agenticai.db.base import Base
from agenticai.db.models import RuntimeSetting
from agenticai.db.runtime_settings import (
    BUS_REDIS_FALLBACK_SETTING_KEY,
    read_bus_redis_fallback_override,
)
from agenticai.db.session import build_engine, build_session_factory


def _session_factory(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path}/runtime-settings.db")
    Base.metadata.create_all(engine)
    return build_session_factory(engine), engine


def test_read_bus_redis_fallback_override_returns_true(tmp_path: Path) -> None:
    session_factory, engine = _session_factory(tmp_path)
    try:
        with Session(bind=engine) as session:
            session.add(
                RuntimeSetting(
                    key=BUS_REDIS_FALLBACK_SETTING_KEY,
                    value="true",
                    description="test",
                )
            )
            session.commit()
        assert read_bus_redis_fallback_override(session_factory) is True
    finally:
        engine.dispose()


def test_read_bus_redis_fallback_override_returns_none_for_invalid_value(tmp_path: Path) -> None:
    session_factory, engine = _session_factory(tmp_path)
    try:
        with Session(bind=engine) as session:
            session.add(
                RuntimeSetting(
                    key=BUS_REDIS_FALLBACK_SETTING_KEY,
                    value="sometimes",
                    description="test",
                )
            )
            session.commit()
        assert read_bus_redis_fallback_override(session_factory) is None
    finally:
        engine.dispose()
