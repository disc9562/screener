import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

import config
from autoresearch import data, engine, score
from autoresearch.bars import DAY_MS, MIN_MS, Bars, resample
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
    # 每天 00 時整點小時大幅跳高，讓「看下一根」在每個整天 cut 都一定改變最後一根的 entry
    c = c * np.where((np.arange(n) * 5 // 60) % 24 == 0, 1.05, 1.0)
    z = np.ones(n)
    return Bars(np.arange(n, dtype=np.int64) * 5 * MIN_MS, c, c * 1.002, c * 0.998, c, z, z)


def _cand(peek):
    def signals(b, aux):
        c = b.c
        entry = np.zeros(len(c), bool)
        if peek:
            entry[:-1] = c[1:] > c[:-1]
        else:
            entry[1:] = c[1:] > c[:-1]
        return {"entry": entry, "stop": c * 0.995}

    return SimpleNamespace(DESCRIPTION="t", ENTRY_TF="1h", SELECT_WINDOW_4H=10, TOP_N=2, signals=signals,
                           update_stop=lambda sig, i, pos: pos.stop, btc_gate=lambda c: True,
                           select_score=lambda w, cut, btc_w: 1.0)


def test_prefix_check_catches_lookahead():
    days = 20
    base = {"A": _walk(1, days * 288), "B": _walk(2, days * 288)}
    end = days * DAY_MS
    gates = {d * DAY_MS: frozenset("AB") for d in range(days)}
    for peek, expect in ((False, True), (True, False)):
        cand = _cand(peek)
        prep = prepare(cand, base, end, base["A"])
        full = simulate(cand, prep, gates, 0.02, 0, end, fee=0.0006)
        assert len(full.trades) > 20
        for d in range(6, 19, 2):
            got = score.prefix_ok(cand, base, gates, 0.02, 0, end, full, [d * DAY_MS], fee=0.0006,
                                  btc5m=base["A"], full_prep=prep)
            assert got is expect, (peek, d)


def _cached_cand(peek):
    cache = {}  # 模擬 candidate 的模組層級快取：BTC 1h 只算一次

    def signals(b, aux):
        if "btc" not in cache:
            cache["btc"] = resample(aux["btc5m"], 60)
        btc = cache["btc"]
        k = np.clip(np.searchsorted(btc.t, b.t, "right") - (0 if peek else 1), 1, len(btc) - 1)
        entry = np.zeros(len(b), bool)
        entry[1:] = b.c[1:] > b.c[:-1]
        return {"entry": entry & (btc.c[k] > btc.c[k - 1]), "stop": b.c * 0.995, "btc_ref": btc.c[k]}

    return SimpleNamespace(DESCRIPTION="t", ENTRY_TF="1h", SELECT_WINDOW_4H=10, TOP_N=2, signals=signals,
                           update_stop=lambda sig, i, pos: pos.stop, btc_gate=lambda c: True,
                           select_score=lambda w, cut, btc_w: 1.0)


def test_prefix_check_reloads_candidate_per_cut():
    # covers: fresh@prefix_ok -> c -> prepare（peek 被訊號比對抓到）、c -> simulate（誠實版走到交易比對）
    days = 20
    base = {"A": _walk(1, days * 288), "B": _walk(2, days * 288)}
    btc = _walk(3, days * 288)
    end = days * DAY_MS
    gates = {d * DAY_MS: frozenset("AB") for d in range(days)}
    for peek, expect in ((False, True), (True, False)):
        cand = _cached_cand(peek)
        prep = prepare(cand, base, end, btc)  # 快取在這裡用完整的 BTC 填好
        full = simulate(cand, prep, gates, 0.02, 0, end, fee=0.0006)
        assert len(full.trades) > 10
        for d in range(6, 19, 3):
            got = score.prefix_ok(cand, base, gates, 0.02, 0, end, full, [d * DAY_MS], fee=0.0006,
                                  btc5m=btc, full_prep=prep, fresh=lambda: _cached_cand(peek))
            assert got is expect, (peek, d)


def test_cli_passes_fresh_loader(monkeypatch, tmp_path):
    # covers: _fresh_candidate@main -> evaluate(fresh=)，而且每次載入都是新的模組
    seen = {}
    monkeypatch.setattr(data, "load_all", lambda: ([], {}))
    monkeypatch.setattr(score, "evaluate",
                        lambda cand, *a, fresh=None, **k: seen.update(cand=cand, fresh=fresh) or {"status": "pass"})
    assert score.main(["--out", str(tmp_path / "o.json"), "--seed", "1"]) == 0
    assert seen["fresh"] is score._fresh_candidate
    assert seen["fresh"]() is not seen["cand"]


def test_cli_always_writes_json(monkeypatch, tmp_path):
    out = tmp_path / "o.json"

    def boom():
        raise RuntimeError("no data")

    monkeypatch.setattr(data, "load_all", boom)
    assert score.main(["--out", str(out), "--seed", "1"]) == 0
    assert json.loads(out.read_text())["status"] == "fail"

    out2 = tmp_path / "o2.json"
    monkeypatch.setattr(data, "load_all", lambda: ([], {}))
    monkeypatch.setattr(score, "evaluate", lambda *a, **k: {"status": "pass", "description": object(), 1.5: np.int64(3), (1,): 2})
    assert score.main(["--out", str(out2), "--seed", "1"]) == 0
    assert json.loads(out2.read_text())["status"] == "fail"

    out3 = tmp_path / "o3.json"
    monkeypatch.setattr(score, "evaluate", lambda *a, **k: {"status": "pass", "description": object(), "n": np.int64(3)})
    assert score.main(["--out", str(out3), "--seed", "1"]) == 0
    assert json.loads(out3.read_text())["n"] == 3
    assert not (tmp_path / "o3.json.tmp").exists()


def test_cli_guard_and_harness_check(monkeypatch, tmp_path):
    bad = tmp_path / "candidate.py"
    bad.write_text("import autoresearch.engine as E\nE.TRANSACTION_FEE_PERCENT = 0\n")
    monkeypatch.setattr(score, "CAND_PATH", str(bad))
    monkeypatch.setattr(data, "load_all", lambda: pytest.fail("guard must stop before loading data"))
    out = tmp_path / "o.json"
    assert score.main(["--out", str(out), "--seed", "7"]) == 0
    js = json.loads(out.read_text())
    assert (js["status"], js["reason"]) == ("fail", "forbidden: import autoresearch.engine at line 1")
    assert (js["fee"], js["stop_pct"], js["seed"]) == (config.TRANSACTION_FEE_PERCENT, config.DISASTER_STOP_PCT, 7)

    monkeypatch.undo()
    monkeypatch.setattr(data, "load_all", lambda: ([], {}))

    def swaps_engine(*a, **k):
        monkeypatch.setattr(engine, "simulate", lambda *a, **k: None)
        return {"status": "pass"}

    monkeypatch.setattr(score, "evaluate", swaps_engine)
    assert score.main(["--out", str(out), "--seed", "1"]) == 0
    assert json.loads(out.read_text())["reason"] == "forbidden: harness modified"


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
    span = dict(fee=0.0006, start_ms=0, end_ms=days * DAY_MS)
    base = {"AAA": _walk(3, days * 288), data.BTC: _walk(4, days * 288)}

    def boom(b, aux):
        raise ValueError("bad")

    bad = _cand(False)
    bad.signals = boom
    out = score.evaluate(bad, base, ["AAA"], baseline=False, seed=1, **span)
    assert out["status"] == "fail" and out["reason"].startswith("error: ValueError")

    few = _cand(False)
    few.signals = lambda b, aux: {"entry": np.isin(np.arange(len(b)), [50, 100, 150, 200, 250]),
                                  "stop": b.c * 0.9}
    out = score.evaluate(few, base, ["AAA"], baseline=True, seed=1, **span)
    assert out["status"] == "fail" and out["reason"] == "trades<=100" and out["trades"] <= 5
