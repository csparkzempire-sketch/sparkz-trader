"""Configuration, presets and validation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.common import settings_from
from app.config import load_config, list_presets, load_preset

router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.get("/config")
def default_config():
    return load_config(env={}).model_dump(mode="json")


@router.get("/presets")
def presets():
    return [{"key": k, "name": n, "config": load_preset(k).model_dump(mode="json")} for k, n in list_presets().items()]


@router.post("/validate")
def validate(body: dict):
    s = settings_from(body.get("preset"), body.get("overrides"))
    return {"valid": True, "config": s.model_dump(mode="json")}
