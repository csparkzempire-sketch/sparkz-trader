from fastapi import APIRouter

from app.api.state import locked, runner

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("")
@locked
def account():
    r = runner().robot
    return {**r.account.snapshot(),
            "equity_curve": [{"time": p.time.isoformat(), "equity": p.equity, "balance": p.balance}
                             for p in r.equity_curve[-1000:]],
            "fills": r.ledger.to_list(100)}
