"""
Command line for the research system (run from backend/):

  python -m app.cli download --symbol XAUUSD --timeframe 15m     (or --all)
  python -m app.cli import-csv FILE --symbol XAUUSD --timeframe 15m --tz Etc/GMT-2
  python -m app.cli backtest [--preset B_atr_grid] [--set grid.atr_multiplier=0.8 ...] [--save]
  python -m app.cli compare  [--presets A_fixed_grid B_atr_grid ...] [--symbol .. --timeframe ..]
  python -m app.cli stress | robustness | sensitivity | walk-forward  [--preset ..] [--set ..]
  python -m app.cli paper-create NAME [--preset ..] | paper-step NAME | paper-status NAME | paper-resume NAME
  python -m app.cli ml-study [--symbol XAUUSD --timeframe 1h]

--set takes dotted config paths; values are parsed as YAML (numbers, true/false, strings).
"""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from app.config import load_config, load_preset


def _overrides(pairs: list[str] | None, symbol=None, timeframe=None, start=None, end=None) -> dict:
    out: dict = {}
    for pair in pairs or []:
        key, _, raw = pair.partition("=")
        d = out
        parts = key.strip().split(".")
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = yaml.safe_load(raw)
    m = {k: v for k, v in {"symbol": symbol, "timeframe": timeframe, "start": start, "end": end}.items() if v}
    if m:
        out.setdefault("market", {}).update(m)
    return out


def _settings(a):
    ov = _overrides(a.set, a.symbol, a.timeframe, getattr(a, "start", None), getattr(a, "end", None))
    return load_preset(a.preset, ov) if a.preset else load_config(env={}, overrides=ov)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="sparkz-grid")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, preset=True):
        if preset:
            sp.add_argument("--preset")
            sp.add_argument("--set", nargs="*", help="dotted.path=value")
        sp.add_argument("--symbol")
        sp.add_argument("--timeframe")

    d = sub.add_parser("download"); common(d, False); d.add_argument("--all", action="store_true")
    ic = sub.add_parser("import-csv"); ic.add_argument("path"); common(ic, False); ic.add_argument("--tz", default="UTC")
    bt = sub.add_parser("backtest"); common(bt); bt.add_argument("--start"); bt.add_argument("--end"); bt.add_argument("--save", action="store_true")
    cp = sub.add_parser("compare"); cp.add_argument("--presets", nargs="*"); common(cp, False); cp.add_argument("--set", nargs="*")
    for name in ("stress", "robustness", "sensitivity", "walk-forward"):
        sp = sub.add_parser(name); common(sp)
        if name == "robustness":
            sp.add_argument("--runs", type=int, default=40)
    pc = sub.add_parser("paper-create"); pc.add_argument("name"); common(pc)
    for name in ("paper-step", "paper-status", "paper-resume"):
        sub.add_parser(name).add_argument("name")
    ml = sub.add_parser("ml-study"); common(ml)
    a = p.parse_args(argv)

    if a.cmd == "download":
        from app.data.downloader import download
        from app.data.instruments import INSTRUMENTS
        from app.data.repository import save_candles

        pairs = [(s, tf) for s in INSTRUMENTS for tf in ("15m", "1h")] if a.all else \
            [((a.symbol or "XAUUSD").upper(), a.timeframe or "15m")]
        for sym, tf in pairs:
            df, info = download(sym, tf)
            n = save_candles(df, sym, tf, info["source"])
            print(f"{sym} {tf}: {n} bars {info['first']} -> {info['last']} ({info['source']}){'  NOTE: ' + info['note'] if info['note'] else ''}")
    elif a.cmd == "import-csv":
        from app.data.csv_import import read_csv
        from app.data.repository import save_candles

        df, info = read_csv(a.path, a.symbol, a.timeframe, a.tz)
        save_candles(df, a.symbol.upper(), a.timeframe, info["source"])
        _print(info)
    elif a.cmd == "backtest":
        from app.backtest.runner import run_backtest
        from app.backtest.summary import pick

        res = run_backtest(_settings(a), save=a.save)
        _print({"id": res.backtest_id, **pick(res.metrics), "close_reasons": res.metrics["close_reasons"],
                "by_regime": res.metrics["by_regime"]})
    elif a.cmd == "compare":
        from app.backtest.comparison import compare

        out = compare(a.presets, _overrides(a.set, a.symbol, a.timeframe))
        for r in out["rows"]:
            r.pop("curve", None)
        _print(out)
    elif a.cmd in ("stress", "robustness", "sensitivity", "walk-forward"):
        from app.backtest.runner import prepare_features
        from app.data.repository import load_candles

        s = _settings(a)
        if a.cmd == "stress":
            from app.backtest.stress_test import run_stress
            _print(run_stress(s))
            return
        f = prepare_features(load_candles(s.market.symbol, s.market.timeframe), s)
        if a.cmd == "robustness":
            from app.backtest.studies import robustness_study
            _print(robustness_study(s, f, a.runs))
        elif a.cmd == "sensitivity":
            from app.backtest.monte_carlo import sensitivity
            _print(sensitivity(s, f))
        else:
            from app.backtest.studies import walk_forward_study
            _print(walk_forward_study(s, f))
    elif a.cmd.startswith("paper-"):
        from app.paper import simulator

        if a.cmd == "paper-create":
            simulator.create_account(a.name, _settings(a))
            _print(simulator.status(a.name))
        elif a.cmd == "paper-step":
            _print(simulator.step(a.name))
        elif a.cmd == "paper-status":
            st = simulator.status(a.name)
            st.pop("equity_curve", None)
            _print(st)
        else:
            print(simulator.resume(a.name))
    elif a.cmd == "ml-study":
        from app.research.ml import ml_study
        _print(ml_study(_settings(a)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
