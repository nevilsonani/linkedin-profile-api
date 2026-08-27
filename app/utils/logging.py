"""Structured JSON logging with automatic secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# Anything that looks like a session token gets masked before it can reach a log
# sink. li_at values are long base64-ish blobs; JSESSIONID is `ajax:<digits>`.
_SECRET_PATTERNS = [
    re.compile(r"(li_at=)[^;\s]+", re.IGNORECASE),
    re.compile(r"(JSESSIONID=\"?)[^;\s\"]+", re.IGNORECASE),
    re.compile(r"(csrf-token['\"]?\s*[:=]\s*['\"]?)[^,;\s'\"]+", re.IGNORECASE),
    re.compile(r"(X-API-Key['\"]?\s*[:=]\s*['\"]?)[^,;\s'\"]+", re.IGNORECASE),
]


def _redact(value: str) -> str:
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1***REDACTED***", value)
    return value


def _redact_processor(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key, val in list(event_dict.items()):
        if isinstance(val, str):
            event_dict[key] = _redact(val)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric,
        force=True,
    )
    # httpx logs every request at INFO including full URLs; keep it quieter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "linkedin-api") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
