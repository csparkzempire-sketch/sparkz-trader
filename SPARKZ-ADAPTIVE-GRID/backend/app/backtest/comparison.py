"""
Strategy comparison lab: strategies A-E (plus a single-position control) run
separately, on the same market, dates, costs and risk limits, and are shown
side by side. Nothing is combined and nothing is ranked "best": the table is
the measurement, and which trade-off is acceptable is the user's call.
"""

from __future__ import annotations

from app.backtest.parallel import run_many
from app.backtest.runner import prepare_features
from app.backtest.summary import pick
from app.config import list_presets, load_preset
from app.data.repository import load_candles

NOTE = ("Each strategy was run independently on identical data, costs and risk limits. Figures describe this "
        "sample only; none of them is a forecast, and no strategy is declared superior.")


def compare(preset_keys: list[str] | None = None, shared: dict | None = None, candles=None, workers=None) -> dict:
    keys = preset_keys or list(list_presets())
    settings = [load_preset(k, shared) for k in keys]
    s0 = settings[0]
    if candles is None:
        candles = load_candles(s0.market.symbol, s0.market.timeframe)
    features = prepare_features(candles, s0)   # presets share the analysis section
    results = run_many(settings, features, workers)
    rows = []
    for k, s, r in zip(keys, settings, results):
        if "error" in r:
            rows.append({"key": k, "name": s.name, "error": r["error"]})
            continue
        m = r["metrics"]
        rows.append({"key": k, "name": s.name, "high_risk": s.sizing.mode.value == "MARTINGALE", **pick(m),
                     "by_regime": m["by_regime"], "by_vol_regime": m["by_vol_regime"], "curve": r["curve"],
                     "grid": s.grid.mode.value, "sizing": s.sizing.mode.value})
    return {"symbol": s0.market.symbol, "timeframe": s0.market.timeframe, "rows": rows, "note": NOTE}
