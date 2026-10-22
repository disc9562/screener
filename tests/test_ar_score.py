import math
from types import SimpleNamespace

import numpy as np
import pytest

from autoresearch import data, score
from autoresearch.bars import DAY_MS, MIN_MS, Bars
from autoresearch.engine import SimResult, Trade, prepare, simulate


def _res(dd, bankrupt=False):
    return SimResult([], np.zeros(0), np.zeros(0), 10_000.0, dd, bankrupt)


def _tr(sym="A", ret=0.0, pnl=0.0, exit_ms=0, px=100.0, stop=80.0):
    return Trade(sym, 0, exit_ms, px, px, stop, 1.0, pnl, ret, "STOP")


def test_calibrate_converges_to_band():
    c = score.calibrate(lambda r: _res(5 * r))
    assert c.status == "in_band" and c.runs == 2 and math.isclose(c.r, 0.05)


def test_calibrate_capped_when_mdd_low_at_max_r():
    c = score.calibrate(lambda r: _res(2 * r))
    assert c.status == "capped" and c.r == 0.05


def test_calibrate_unreachable_and_bankrupt():
    c = score.calibrate(lambda r: _res(0.20 if r < 0.0445 else 0.30))
    assert c.status == "unreachable" and c.runs == 5
    b = score.calibrate(lambda r: _res(0.1, bankrupt=True))
    assert b.status == "bankrupt" and b.runs == 1


def test_coin_drawdowns_definition_b():
    rets = [("A", 0.10), ("A", -0.20), ("A", -0.20), ("A", 0.05), ("B", -0.2), ("B", -0.2), ("B", -0.2)]
    trades = [_tr(s, r, exit_ms=i) for i, (s, r) in enumerate(rets)]
    for ts in (trades, trades[::-1]):
        d = score.coin_drawdowns(ts)
        assert d["A"] == pytest.approx(1 - 0.704 / 1.1)
        assert d["B"] == pytest.approx(1 - 0.8 ** 3)


def test_fixed_pct_stop_detects_95_percent():
    def mk(same, other):
        return [_tr(stop=80.0)] * same + [_tr(stop=60.0 + i) for i in range(other)]

    assert score.fixed_pct_stop(mk(95, 5)) is True
    assert score.fixed_pct_stop(mk(94, 6)) is False
    rng = np.random.default_rng(0)
    assert score.fixed_pct_stop([_tr(stop=100 * (1 - d)) for d in rng.uniform(0.02, 0.2, 100)]) is False


def _walk(seed, n):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    z = np.ones(n)
    return Bars(np.arange(n, dtype=np.int64) * 5 * MIN_MS, c, c * 1.002, c * 0.998, c, z, z)


def _cand(peek):
    def signals(b):
        c = b.c
        entry = np.zeros(len(c), bool)
        if peek:
            entry[:-1] = c[1:] > c[:-1]
        else:
            entry[1:] = c[1:] > c[:-1]
        return {"entry": entry, "stop": c * 0.995}

    return SimpleNamespace(DESCRIPTION="t", ENTRY_TF="1h", SELECT_WINDOW_4H=10, TOP_N=2, signals=signals,
                           update_stop=lambda sig, i, pos: pos.stop, btc_gate=lambda c: True,
                           select_score=lambda w, cut: 1.0)


def test_prefix_check_catches_lookahead():
    days = 20
    base = {"A": _walk(1, days * 288), "B": _walk(2, days * 288)}
    end = days * DAY_MS
    gates = {d * DAY_MS: frozenset("AB") for d in range(days)}
    cuts = [10 * DAY_MS, 15 * DAY_MS]
    for peek, expect in ((False, True), (True, False)):
        cand = _cand(peek)
        full = simulate(cand, prepare(cand, base, end), gates, 0.02, 0, end)
        assert len(full.trades) > 20
        assert score.prefix_ok(cand, base, gates, 0.02, 0, end, full, cuts) is expect


def test_yearly_and_coin_share():
    # 2021: 10000 -> 12000 -> 9000 -> 12000；2022: 12000 -> 10800
    t = np.array([1_620_000_000_000, 1_625_000_000_000, 1_630_000_000_000, 1_650_000_000_000], dtype=np.int64)
    eq = np.array([12000.0, 9000.0, 12000.0, 10800.0])
    out = score.yearly(t, eq, 10_000.0)
    assert [o["year"] for o in out] == [2021, 2022]
    assert out[0]["return"] == pytest.approx(0.2) and out[0]["mdd"] == pytest.approx(0.25)
    assert out[1]["return"] == pytest.approx(-0.1)
    ts = [_tr("A", pnl=300), _tr("B", pnl=100), _tr("C", pnl=-50)]
    assert score.coin_profit_share(ts) == [("A", 0.75), ("B", 0.25)]


def test_evaluate_reports_error_and_few_trades(monkeypatch):
    days = 12
    monkeypatch.setattr(data, "START_MS", 0)
    monkeypatch.setattr(data, "END_MS", days * DAY_MS)
    base = {"AAA": _walk(3, days * 288), data.BTC: _walk(4, days * 288)}

    def boom(b):
        raise ValueError("bad")

    bad = _cand(False)
    bad.signals = boom
    out = score.evaluate(bad, base, ["AAA"], baseline=False, seed=1)
    assert out["status"] == "fail" and out["reason"].startswith("error: ValueError")

    few = _cand(False)
    few.signals = lambda b: {"entry": np.isin(np.arange(len(b)), [50, 100, 150, 200, 250]),
                             "stop": b.c * 0.9}
    out = score.evaluate(few, base, ["AAA"], baseline=True, seed=1)
    assert out["status"] == "fail" and out["reason"] == "trades<=100" and out["trades"] <= 5
