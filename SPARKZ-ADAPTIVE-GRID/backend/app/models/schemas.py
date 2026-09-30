"""Request bodies for the API. Responses are plain JSON built by the services."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DownloadRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "15m"


class RunRequest(BaseModel):
    preset: str | None = Field(None, description="config/strategies/<preset>.yaml; omit for default.yaml")
    overrides: dict = Field(default_factory=dict, description="nested config overrides, e.g. {'grid': {'mode': 'ATR'}}")
    name: str | None = None
    save: bool = True


class CompareRequest(BaseModel):
    presets: list[str] | None = None
    shared: dict = Field(default_factory=dict, description="market, costs and risk applied to every strategy")


class RobustnessRequest(RunRequest):
    runs: int = Field(40, ge=4, le=400)


class PaperCreateRequest(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    preset: str | None = None
    overrides: dict = Field(default_factory=dict)
