"""Helpers for runtime configuration sourced from persistent storage."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from agenticai.db.models import RuntimeSetting

logger = logging.getLogger(__name__)

BUS_REDIS_FALLBACK_SETTING_KEY = "bus.redis_fallback_to_inmemory"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def read_bus_redis_fallback_override(session_factory: sessionmaker[Session]) -> bool | None:
    """Read optional Redis fallback override from runtime settings table."""
    try:
        with session_factory() as session:
            setting = session.get(RuntimeSetting, BUS_REDIS_FALLBACK_SETTING_KEY)
    except SQLAlchemyError:
        logger.warning(
            "Unable to load runtime setting '%s'; using environment defaults",
            BUS_REDIS_FALLBACK_SETTING_KEY,
            exc_info=True,
        )
        return None

    if setting is None:
        return None

    parsed = _parse_bool(setting.value)
    if parsed is None:
        logger.warning(
            "Ignoring runtime setting '%s' with unsupported value '%s'",
            BUS_REDIS_FALLBACK_SETTING_KEY,
            setting.value,
        )
    return parsed
