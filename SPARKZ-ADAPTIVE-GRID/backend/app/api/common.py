"""Shared helpers for the API routers."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import load_config, load_preset


def settings_from(preset: str | None, overrides: dict | None):
    """Build validated settings; configuration errors become HTTP 422 with the reason."""
    try:
        if preset:
            return load_preset(preset, overrides or None)
        return load_config(env={}, overrides=overrides or None)
    except ValidationError as exc:
        raise HTTPException(422, detail=[e["msg"] for e in exc.errors()]) from None
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
