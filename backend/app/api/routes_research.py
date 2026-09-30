from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.research.studies import load_results

router = APIRouter()


@router.get("")
def get_research() -> dict:
    """Saved results of the market and settings-sensitivity studies (see app.research.studies)."""
    results = load_results()
    if results is None:
        raise HTTPException(status_code=404, detail="No research results yet. Run: python -m app.cli research")
    return results
