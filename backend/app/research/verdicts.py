"""
Research verdict per paper account, for display next to its paper results.

Combines the two checks run on each account's backtest:
- random-entry check (random_entry.json, RANDOM_ENTRY_CHECK.md): does the strategy beat
  random entries with the same exits?
- cost stress test (cost_stress.json, COST_STRESS_CHECK.md, hourly accounts only): does
  the edge survive 2x the modeled trading costs?

Groups:
  tested   beat random entries and survive 2x costs
  thin     beat random entries but fail at 2x costs
  control  no evidence of beating random entries: the result mostly tracks the market

Information only: nothing here affects trading or the pass/fail targets. An account
created as a fee copy of another (name ending "_fee<bps>") shows the original's verdict.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent
REPORTS = {
    "random_entry": "backend/app/research/RANDOM_ENTRY_CHECK.md",
    "cost_stress": "backend/app/research/COST_STRESS_CHECK.md",
}
LABELS = {
    "tested": "Passed research checks",
    "thin": "Thin edge: fails at 2× costs",
    "control": "Control: no edge over random entries",
}


def _load(name: str) -> dict:
    try:
        return {a["account"]: a for a in json.loads((HERE / name).read_text())["accounts"]}
    except (OSError, ValueError, KeyError):
        return {}


@lru_cache(maxsize=1)
def _results() -> tuple[dict, dict]:
    return _load("random_entry.json"), _load("cost_stress.json")


def _ordinal(n: float) -> str:
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def research_verdict(account_name: str) -> dict | None:
    random_entry, cost_stress = _results()
    base = re.sub(r"_fee\d+$", "", account_name)
    re_ = random_entry.get(base)
    if re_ is None:
        return None
    cs = cost_stress.get(base)
    beat_random = bool(re_["passed"])
    pct = min(t["real_percentile"] for t in re_["tests"].values())
    if not beat_random:
        group = "control"
    elif cs is not None and not cs["passed"]:
        group = "thin"
    else:
        group = "tested"

    parts = [f"Beat random entries ({_ordinal(pct)} percentile)" if beat_random
             else f"Did not beat random entries ({_ordinal(pct)} percentile; needs >95th)"]
    if cs is not None:
        be = cs.get("break_even_multiplier")
        bp = cs.get("break_even_round_trip_bp")
        parts.append(("survives" if cs["passed"] else "fails at") + " 2× costs"
                     + (f", break-even at {be:.1f}× ({bp:.0f} bp per round trip)" if be else ""))
    return {
        "group": group,
        "label": LABELS[group],
        "summary": "; ".join(parts) + ".",
        "beat_random_entries": beat_random,
        "random_entry_percentile": pct,
        "survives_2x_costs": None if cs is None else bool(cs["passed"]),
        "break_even_multiplier": None if cs is None else cs.get("break_even_multiplier"),
        "break_even_round_trip_bp": None if cs is None else cs.get("break_even_round_trip_bp"),
        "round_trip_cost_bp": None if cs is None else cs.get("round_trip_cost_bp_at_1x"),
        "reports": [REPORTS["random_entry"]] + ([REPORTS["cost_stress"]] if cs is not None else []),
    }
