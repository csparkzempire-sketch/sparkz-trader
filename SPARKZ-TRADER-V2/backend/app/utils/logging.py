"""Process logging with secret redaction: credential values from the environment never reach a log line."""

from __future__ import annotations

import logging
import os
import re

SECRET_KEYS = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|ACCOUNT_ID)$", re.I)


def secret_values() -> list[str]:
    return [v for k, v in os.environ.items() if SECRET_KEYS.search(k) and len(v) >= 4]


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in secret_values():
            if s in msg:
                msg = msg.replace(s, "***")
        record.msg, record.args = msg, None
        return True


def setup(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger()
    if not any(isinstance(f, RedactSecrets) for h in root.handlers for f in h.filters):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.addFilter(RedactSecrets())
        root.addHandler(h)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            h.addFilter(RedactSecrets())
    return logging.getLogger("sparkz")
