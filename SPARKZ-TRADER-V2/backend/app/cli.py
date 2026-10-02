"""
Command line.

  python -m app.cli download     --symbol XAUUSD --timeframe 15m
  python -m app.cli download     --source oanda --timeframe 1m --start 2024-01-01   (read-only broker history)
  python -m app.cli import-csv   FILE --symbol XAUUSD --timeframe 15m
  python -m app.cli backtest     [--preset video_style] [--data stored|synthetic:<scenario>] [--set key=value ...]
  python -m app.cli stress       [--preset ...]
  python -m app.cli walk-forward [--preset ...] [--space grid.atr_multiplier=0.5,1.0 ...] [--folds 3]
  python -m app.cli lab          [--preset ...] --space key=v1,v2 ...
  python -m app.cli paper        [--preset ...] [--minutes 60]          (no API: prints status lines)
  python -m app.cli presets
  python -m app.cli archive      [--symbol XAUUSD] [--timeframes 1m,5m]   (daily: saves completed days)
  python -m app.cli archive-status

Reports are written as JSON to reports/<kind>/<timestamp>_<preset>.json.
Every run is a SIMULATION. No command can place a real order.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from app.config import REPORTS_DIR, list_presets, load_env_file, load_settings

load_env_file()


def _value(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.lower() in ("none", "null"):
        return None
    return v


def _overrides(pairs: list[str]) -> dict:
    out: dict = {}
    for p in pairs or []:
        k, v = p.split("=", 1)
        d = out
        parts = k.split(".")
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = _value(v)
    return out


def _space(pairs: list[str]) -> dict:
    return {k: [_value(x) for x in v.split(",")] for k, v in (p.split("=", 1) for p in pairs or [])}


def _settings(a):
    return load_settings(a.preset, overrides=_overrides(a.set))


def _data(s, spec: str, bars: int, source: str = "yahoo"):
    from app.market.history import load_history
    from app.market.providers.mock_provider import generate_candles

    if spec.startswith("synthetic"):
        scenario = spec.split(":", 1)[1] if ":" in spec else "normal"
        return generate_candles(s.market.symbol, s.market.timeframe, bars, scenario, seed=1), f"synthetic:{scenario}"
    df = load_history(s.market.symbol, s.market.timeframe, source)
    return df, f"stored {source} {s.market.symbol} {s.market.timeframe} ({len(df)} candles)"


def _save(kind: str, name: str, payload: dict) -> str:
    d = REPORTS_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{name}.json"
    p.write_text(json.dumps(payload, indent=1, default=str))
    return str(p)


def cmd_backtest(a) -> None:
    from app.backtest.engine import run_backtest
    from app.backtest.metrics import report

    from app.market.history import load_history

    s = _settings(a)
    df, label = _data(s, a.data, a.bars, a.source)
    path = None
    if a.path:
        path = load_history(s.market.symbol, a.path, a.source)
        label += f", intrabar path from {a.path} candles where available"
    rep = report(run_backtest(s, df, label, path_candles=path))
    m = rep["metrics"]
    print(f"{s.name} on {label}: {m['start']} -> {m['end']}  (SIMULATED)")
    for k in ("net_pnl", "return_pct", "baskets", "win_rate_pct", "profit_factor", "largest_basket_loss",
              "max_drawdown_pct", "worst_floating_pnl", "max_positions_in_basket", "close_reasons"):
        print(f"  {k:28s} {m[k]}")
    print(f"  {'buy_and_hold_pct':28s} {(m['buy_and_hold'] or {}).get('price_change_pct')}")
    print("report:", _save("backtests", s.name, rep))


def cmd_stress(a) -> None:
    from app.backtest.stress_test import run_all

    s = _settings(a)
    df = None
    if a.data != "synthetic":
        df, _ = _data(s, a.data, a.bars, a.source)
    out = run_all(s, df, a.data, bars=a.bars)
    for k, v in out["market_scenarios"].items():
        print(f"  {k:14s} mean {v['mean_net_pnl']:9.2f}  worst basket {v['worst_basket']:9.2f}  "
              f"dd {v['worst_drawdown_pct']:7.2f}%  full grids {v['baskets_at_max_positions']}")
    for k, v in out["critical_failure"]["variants"].items():
        print(f"  critical/{k:28s} {v['outcome']:16s} pnl {v['realized_pnl']}  worst float {v['worst_floating_pnl']}")
    print("report:", _save("stress", s.name, out))


def cmd_walk_forward(a) -> None:
    from app.backtest.walk_forward import walk_forward

    s = _settings(a)
    df, label = _data(s, a.data, a.bars, a.source)
    out = walk_forward(s, df, _space(a.space) or None, folds=a.folds, label=label)
    print(json.dumps(out["summary"], indent=1))
    print("report:", _save("walk_forward", s.name, out))


def cmd_lab(a) -> None:
    from app.backtest.walk_forward import parameter_lab

    s = _settings(a)
    df, label = _data(s, a.data, a.bars, a.source)
    out = parameter_lab(s, df, _space(a.space), label=label)
    print("chosen:", out["chosen"], "test:", out["test"], "warnings:", out["warnings"])
    print("report:", _save("lab", s.name, out))


def cmd_paper(a) -> None:
    from app.database.repository import Repository
    from app.market.data_provider import make_provider
    from app.paper.runner import PaperRunner

    s = _settings(a)
    runner = PaperRunner(s, make_provider(s), Repository())
    runner.start()
    end = time.time() + a.minutes * 60

    async def main():
        task = asyncio.create_task(runner.run())
        while time.time() < end:
            await asyncio.sleep(max(s.market.poll_seconds, 5))
            d = runner.dashboard(0)
            acc = d["account"]
            b = d["basket"]
            print(f"{datetime.now(timezone.utc):%H:%M:%S} {d['status']:16s} mid {d['market']['mid'] if d['market'] else '-'} "
                  f"equity {acc['equity']:.2f} basket {b['positions'] if b else 0} pos "
                  f"float {acc['floating_pnl']:+.2f}  health {d['health']['overall']}", flush=True)
        runner.stop_loop()
        await task
    asyncio.run(main())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sparkz-v2", description="SPARKZ TRADER V2 (simulation only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, data="stored"):
        p.add_argument("--preset", default=None)
        p.add_argument("--set", action="append", default=[], help="override, e.g. grid.distance=3")
        p.add_argument("--data", default=data, help="stored | synthetic[:scenario]")
        p.add_argument("--bars", type=int, default=3000, help="synthetic series length")
        p.add_argument("--source", default="yahoo", choices=["yahoo", "oanda"], help="stored data source")

    p = sub.add_parser("download")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--source", default="yahoo", choices=["yahoo", "oanda"])
    p.add_argument("--start", default=None, help="oanda: first date, e.g. 2024-01-01")
    p.add_argument("--end", default=None)
    p = sub.add_parser("import-csv")
    p.add_argument("file")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", default="15m")
    p = sub.add_parser("backtest")
    common(p)
    p.add_argument("--path", default=None, help="finer stored timeframe for the intrabar price path, e.g. 1m")
    common(sub.add_parser("stress"), data="synthetic")
    p = sub.add_parser("walk-forward")
    common(p)
    p.add_argument("--space", action="append", default=[])
    p.add_argument("--folds", type=int, default=3)
    p = sub.add_parser("lab")
    common(p)
    p.add_argument("--space", action="append", default=[], required=True)
    p = sub.add_parser("paper")
    common(p)
    p.add_argument("--minutes", type=float, default=60)
    sub.add_parser("presets")
    p = sub.add_parser("archive", help="save completed days of 1m/5m candles to data/archive (run daily)")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframes", default="1m,5m")
    p = sub.add_parser("archive-status")
    p.add_argument("--symbol", default="XAUUSD")
    a = ap.parse_args(argv)

    if a.cmd == "download":
        from app.market.history import download
        df, rep = download(a.symbol, a.timeframe, a.source, a.start, a.end,
                           progress=lambda n, t: print(f"  page {n}: up to {t:%Y-%m-%d %H:%M}", flush=True)
                           if n % 20 == 0 else None)
        print(rep, df["timestamp"].min(), "->", df["timestamp"].max())
    elif a.cmd == "import-csv":
        from app.market.history import import_csv
        print(import_csv(a.file, a.symbol, a.timeframe)[1])
    elif a.cmd == "archive":
        from app.market.archive import collect, status
        res = collect(a.symbol, tuple(a.timeframes.split(",")))
        failed = False
        for tf, r in res.items():
            if r["error"]:
                failed = True
                print(f"{a.symbol} {tf}: ERROR {r['error']}")
            else:
                new = ", ".join(r["written"]) or "none"
                print(f"{a.symbol} {tf}: {len(r['written'])} new day(s) ({new}); {r['skipped_existing']} already archived")
        for tf, st in status(a.symbol).items():
            print(f"  archive {tf}: {st['days']} day(s), {st['first']} -> {st['last']}")
        if failed:
            sys.exit(1)
    elif a.cmd == "archive-status":
        from app.market.archive import status
        for tf, st in status(a.symbol).items():
            print(f"{a.symbol} {tf}: {st['days']} day(s), {st['first']} -> {st['last']}")
    elif a.cmd == "presets":
        for p in list_presets():
            s = load_settings(p, env={})
            print(f"{p:22s} grid {s.grid.mode.value:16s} sizing {s.sizing.mode.value:10s} "
                  f"target {s.target.mode.value:14s} max pos {s.risk.max_positions}"
                  + ("  HIGH RISK" if s.high_risk else ""))
    else:
        {"backtest": cmd_backtest, "stress": cmd_stress, "walk-forward": cmd_walk_forward, "lab": cmd_lab,
         "paper": cmd_paper}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
