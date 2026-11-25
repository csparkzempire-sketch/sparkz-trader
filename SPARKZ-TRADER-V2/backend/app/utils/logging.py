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
        secrets = [s for s in secret_values() if s in record.getMessage()]
        if not secrets:
            return True                       # untouched: formatters such as uvicorn's read record.args

        def clean(v):
            if isinstance(v, str):
                for s in secrets:
                    v = v.replace(s, "***")
            return v
        record.msg = clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(clean(v) for v in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: clean(v) for k, v in record.args.items()}
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
