"""Paper accounts: virtual money only."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import NoResultFound

from app.api.common import settings_from
from app.data.downloader import DownloadError
from app.models.schemas import PaperCreateRequest
from app.paper import simulator

router = APIRouter(prefix="/paper", tags=["paper"])


@router.get("/accounts")
def accounts():
    return simulator.list_accounts()


@router.post("/accounts")
def create(req: PaperCreateRequest):
    s = settings_from(req.preset, req.overrides)
    try:
        simulator.create_account(req.name, s)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return simulator.status(req.name)


@router.post("/{name}/step")
def step(name: str):
    try:
        return simulator.step(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except (DownloadError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/{name}")
def get(name: str):
    try:
        return simulator.status(name)
    except NoResultFound:
        raise HTTPException(404, f"no paper account named {name!r}") from None


@router.post("/{name}/resume")
def resume(name: str):
    try:
        return {"result": simulator.resume(name)}
    except NoResultFound:
        raise HTTPException(404, f"no paper account named {name!r}") from None
