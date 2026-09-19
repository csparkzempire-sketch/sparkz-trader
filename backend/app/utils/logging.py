"""
Structured logging for SPARKZ TRADER.

Rules:
- Never log API keys, secrets, or full .env contents.
- Every log line is structured (JSON-ish key=value) so it can be grepped
  or piped into a log aggregator later.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_SECRET_KEYS = {"api_key", "apikey", "secret", "token", "password", "broker_key"}


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        lowered = msg.lower()
        for key in _SECRET_KEYS:
            if key in lowered:
                return f"[REDACTED LOG LINE CONTAINING POSSIBLE SECRET KEY '{key}']"
        return msg


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    handler = logging.StreamHandler(sys.stdout)
    formatter = _RedactingFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger


def kv(**fields: Any) -> str:
    """Format keyword args as key=value pairs for structured log messages."""
    return " ".join(f"{k}={v!r}" for k, v in fields.items())
