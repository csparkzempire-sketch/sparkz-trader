import json

import app.research.verdicts as v


def _setup(tmp_path, monkeypatch, random_entry, cost_stress):
    (tmp_path / "random_entry.json").write_text(json.dumps({"accounts": random_entry}))
    (tmp_path / "cost_stress.json").write_text(json.dumps({"accounts": cost_stress}))
    monkeypatch.setattr(v, "HERE", tmp_path)
    v._results.cache_clear()


def _re(name, passed, pct):
    return {"account": name, "passed": passed, "tests": {"random_timing": {"real_percentile": pct}}}


def _cs(name, passed, be):
    return {"account": name, "passed": passed, "break_even_multiplier": be, "break_even_round_trip_bp": be * 5,
            "round_trip_cost_bp_at_1x": 5.0}


def test_groups(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           [_re("a", True, 100), _re("b", True, 100), _re("c", False, 93), _re("d", True, 99)],
           [_cs("a", True, 3.0), _cs("b", False, 1.9)])
    assert v.research_verdict("a")["group"] == "tested"
    assert v.research_verdict("b")["group"] == "thin"
    c = v.research_verdict("c")
    assert c["group"] == "control" and "93rd percentile" in c["summary"]
    assert v.research_verdict("d")["group"] == "tested" and v.research_verdict("d")["survives_2x_costs"] is None
    assert v.research_verdict("unknown") is None
    v._results.cache_clear()


def test_fee_copies_show_the_original_accounts_verdict(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [_re("a", True, 100)], [_cs("a", True, 3.0)])
    assert v.research_verdict("a_fee10") == v.research_verdict("a")
    v._results.cache_clear()
