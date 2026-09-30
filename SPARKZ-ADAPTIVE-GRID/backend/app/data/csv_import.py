"""
CSV import, for history longer than Yahoo keeps (e.g. years of 15-minute gold).

Accepted layouts:
- MetaTrader 5 export (History Center / "Bars" export): tab- or comma-separated
  <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>, where
  SPREAD is in points. The per-bar spread is kept and, with
  execution.use_bar_spread, used as that bar's trading cost.
- Generic: timestamp (or date + time), open, high, low, close[, volume][, spread in price units].

MT5 timestamps are in the broker's server time. Pass the offset with `tz`
(e.g. "Etc/GMT-2" for UTC+2) so bars are converted to UTC correctly.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.data.instruments import get_instrument
from app.data.validator import validate


def read_csv(path: str | Path, symbol: str, timeframe: str, tz: str = "UTC") -> tuple[pd.DataFrame, dict]:
    text = Path(path).read_text(errors="replace")
    sep = "\t" if text.split("\n", 1)[0].count("\t") >= 3 else ","
    df = pd.read_csv(path, sep=sep)
    df.columns = [str(c).strip().strip("<>").lower() for c in df.columns]
    mt5 = "tickvol" in df.columns  # MT5 exports always carry TICKVOL; their SPREAD is in points
    if "timestamp" not in df.columns:
        if "date" in df.columns and "time" in df.columns:
            df["timestamp"] = df["date"].astype(str) + " " + df["time"].astype(str)
        elif "date" in df.columns:
            df["timestamp"] = df["date"].astype(str)
        elif "datetime" in df.columns:
            df["timestamp"] = df["datetime"]
        else:
            raise ValueError("CSV needs a timestamp column, or date and time columns")
    ts = pd.to_datetime(df["timestamp"].astype(str).str.replace(".", "-", regex=False), errors="coerce")
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    df["timestamp"] = ts.dt.tz_convert("UTC")
    if "volume" not in df.columns:
        df["volume"] = df.get("tickvol", df.get("vol", 0.0))
    if mt5 and "spread" in df.columns:
        df["spread"] = df["spread"].astype(float) * get_instrument(symbol).point
    out, rep = validate(df, timeframe)
    return out, {**rep.to_dict(), "source": f"csv:{Path(path).name}", "mt5_format": mt5}
