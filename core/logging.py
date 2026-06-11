import json
import os
import logging
from datetime import datetime, timezone

LOG_LEVEL = os.getenv("AUTOSCALER_LOG_LEVEL", "INFO").upper()

_LEVEL_REMAP = {"WARNING": "WARN", "CRITICAL": "ERROR"}


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "time":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level":   _LEVEL_REMAP.get(record.levelname, record.levelname),
            "message": record.getMessage(),
        })


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(_Formatter())

    logger = logging.getLogger("autoscaler")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False

    # route Flask/werkzeug through our JSON formatter
    werk = logging.getLogger("werkzeug")
    werk.handlers.clear()
    werk.addHandler(handler)
    werk.setLevel(logging.WARNING)
    werk.propagate = False

    return logger


log = _setup_logging()
