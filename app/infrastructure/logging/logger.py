"""
Structured JSON logging, per 12_Coding_Standards.md ("Logs must be
structured JSON") and 03_System_Architecture.md's Logging section
("Every action is logged: Authentication, Trades, Risk Events, Errors,
Broker Events, System Changes, Performance").

Usage:
    from app.infrastructure.logging.logger import get_logger
    logger = get_logger(__name__)
    logger.info("order.approved", extra={"order_id": str(order.id), "symbol": "AAPL"})
"""
import datetime as dt
import json
import logging
import sys

from app.config import get_settings

settings = get_settings()

_RESERVED_LOG_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include any extra= fields the caller passed in.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS and not key.startswith("_"):
                try:
                    json.dumps(value)  # only include JSON-serializable extras
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("prometheus")
    root.setLevel(settings.LOG_LEVEL)
    handler = logging.StreamHandler(sys.stdout)
    if settings.LOG_JSON:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    if not name.startswith("prometheus"):
        name = f"prometheus.{name}"
    return logging.getLogger(name)
