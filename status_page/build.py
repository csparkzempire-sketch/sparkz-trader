"""
Build the hosted Paper Desk page (status_page/index.html) from template.html.

The page's live figures come from its database, which the hourly paper-trading
run updates (python -m app.cli paper-snapshot). The research tables are fixed,
so they are embedded at build time from backend/app/research/results.json.

Usage: python status_page/build.py  ->  status_page/index.html, then publish that
file to the existing artifact (https://claude.ai/artifact/Xik2JHzjHWHw4gKiKN7Gb8).
Design reference: Figma file "Sparkz Paper Desk — Classic redesign".
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE.parent / "backend" / "app" / "research" / "results.json"


def slim(results: dict) -> dict:
    return {
        "generated_at": results["generated_at"],
        "markets": [
            {k: m[k] for k in ("symbol", "market", "timeframe", "side", "start", "end", "hold_pct")}
            | {"full": {k: m["full"][k] for k in ("return_pct", "profit_factor", "trades", "max_drawdown_pct")},
               "profitable": m["periods"]["profitable"], "count": m["periods"]["count"], "kind": m["periods"]["kind"]}
            for m in results["markets"]
        ],
        "sensitivity": [
            {k: s[k] for k in ("account", "symbol", "timeframe", "side", "profitable", "count",
                               "min_pct", "max_pct", "median_pct", "default_rank")}
            | {"default_pct": s["default"]["return_pct"]}
            for s in results["sensitivity"]
        ],
    }


if __name__ == "__main__":
    template = (HERE / "template.html").read_text()
    assert "/*RESEARCH*/null" in template, "template is missing the research placeholder"
    data = json.dumps(slim(json.loads(RESULTS.read_text())), separators=(",", ":"))
    out = HERE / "index.html"
    out.write_text(template.replace("/*RESEARCH*/null", data))
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
