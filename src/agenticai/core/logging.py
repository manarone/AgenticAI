import logging
import logging.config

from agenticai.core.request_context import get_request_id

_ALLOWED_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class RequestIdFilter(logging.Filter):
    """Inject request correlation identifier into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id()
        record.request_id = request_id if request_id else "-"
        return True


def configure_logging(level: str) -> None:
    """Configure process-wide logging for the API service."""
    normalized_level = level.upper()
    if normalized_level not in _ALLOWED_LEVELS:
        allowed = ", ".join(sorted(_ALLOWED_LEVELS))
        raise ValueError(f"Invalid LOG_LEVEL '{level}'. Expected one of: {allowed}")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": (
                        "%(asctime)s %(levelname)s %(name)s "
                        "[request_id=%(request_id)s]: %(message)s"
                    ),
                }
            },
            "filters": {
                "request_id": {"()": "agenticai.core.logging.RequestIdFilter"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                    "filters": ["request_id"],
                }
            },
            "root": {
                "level": normalized_level,
                "handlers": ["console"],
            },
        }
    )
