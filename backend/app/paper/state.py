"""
Shared paper trading state.

A single PaperTradingSimulator instance and a single account store, so that
positions/trades opened by the live feed scheduler (app.paper.scheduler)
are visible through the same /paper/* API routes a user queries manually,
and vice versa. In-process only for this version — see the module
docstring in app/api/routes_paper.py for the persistence caveat.
"""

from __future__ import annotations

from app.paper.simulator import PaperAccountState, PaperTradingSimulator

simulator = PaperTradingSimulator()
accounts: dict[str, PaperAccountState] = {}


def get_or_create_account(name: str, starting_balance: float) -> PaperAccountState:
    if name not in accounts:
        accounts[name] = PaperAccountState(balance=starting_balance)
    return accounts[name]


def get_account(name: str) -> PaperAccountState | None:
    return accounts.get(name)
