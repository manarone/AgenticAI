import logging
import logging.config

_ALLOWED_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


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
                    "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {
                "level": normalized_level,
                "handlers": ["console"],
            },
        }
    )
