from types import SimpleNamespace

import numpy as np
import pytest

from autoresearch import engine
from autoresearch.bars import DAY_MS, HOUR_MS, Bars
from autoresearch.engine import CandidateError, Prepared, build_daily_gates, prepare, simulate

N = 10
END = N * HOUR_MS


def _bars(c, l=None, step=HOUR_MS):
    c = np.asarray(c, float)
    l = c - 1 if l is None else np.asarray(l, float)
    n = len(c)
    return Bars(np.arange(n, dtype=np.int64) * step, c, c + 1, l, c, np.ones(n), np.ones(n))


def _empty():
    z = np.zeros(0)
    return Bars(np.zeros(0, np.int64), z, z, z, z, z, z)


def _cand(update=None):
    return SimpleNamespace(update_stop=update or (lambda sig, i, pos: pos.stop))


def _prep(spec):
    """spec: sym -> dict(c, l, entries=[i], stop=float, stops={i: x}, exit=[i])"""
    bars, sig, ex = {}, {}, {}
    for s, d in spec.items():
        b = _bars(d["c"], d.get("l"))
        n = len(b)
        entry = np.zeros(n, bool)
        entry[d.get("entries", [])] = True
        stop = np.full(n, d.get("stop", 50.0), float)
        for i, v in d.get("stops", {}).items():
            stop[i] = v
        mask = np.zeros(n, bool)
        mask[d.get("exit", [])] = True
        bars[s], sig[s], ex[s] = b, {"entry": entry, "stop": stop}, mask
    return Prepared(HOUR_MS, bars, sig, ex, dict(bars))


def _gate(*syms):
    return {0: frozenset(syms)}


@pytest.fixture(autouse=True)
def no_fee(monkeypatch):
    monkeypatch.setattr(engine, "TRANSACTION_FEE_PERCENT", 0.0)


def test_build_daily_gates_window_and_btc_gate():
    seen = []

    def score(w, cut):
        last = w.c[-1]
        seen.append((len(w), int(w.t[-1]), cut, last))
        return None if last == 7 else last / 100

    cand = SimpleNamespace(SELECT_WINDOW_4H=100, TOP_N=1, select_score=score,
                           btc_gate=lambda c: len(c) != 21)
    h4 = 4 * HOUR_MS
    bars_4h = {"B": _bars([100.0] * 400, step=h4), "A": _bars([100.0] * 400, step=h4),
               "C": _bars([50.0] * 50, step=h4), "D": _bars([7.0] * 400, step=h4), "E": _empty()}
    btc = _bars(np.ones(30), step=DAY_MS)
    gates = build_daily_gates(cand, bars_4h, btc, 20 * DAY_MS, 22 * DAY_MS)
    assert gates == {20 * DAY_MS: frozenset({"A"}), 21 * DAY_MS: frozenset()}
    assert all(n <= 100 and last < cut for n, last, cut, _ in seen)
    full = [(n, last, cut) for n, last, cut, lv in seen if lv == 100]
    assert len(full) == 2 and all(n == 100 and last == cut - 4 * HOUR_MS for n, last, cut in full)


def test_simulate_sizes_by_risk_and_fills_stop():
    l = [99.0] * N
    l[4] = 89
    p = _prep({"A": dict(c=[100.0] * N, l=l, entries=[2], stop=90.0)})
    res = simulate(_cand(), p, _gate("A"), 0.04, 0, END)
    (t,) = res.trades
    assert (t.units, t.exit_px, t.reason, t.pnl) == (40, 90, "STOP", -400)
    assert res.final_equity == pytest.approx(9600)


def test_stop_only_ratchets_up_from_next_bar():
    l = [99.0] * N
    l[3], l[4], l[5] = 94, 96, 94.5
    p = _prep({"A": dict(c=[100.0] * N, l=l, entries=[2], stop=60.0)})
    cand = _cand(lambda sig, i, pos: {3: 95.0, 4: 80.0}.get(i, pos.stop))
    (t,) = simulate(cand, p, _gate("A"), 0.04, 0, END).trades
    assert (t.exit_px, t.reason, t.exit_ms) == (95, "STOP", 6 * HOUR_MS)


def test_update_stop_clamped_to_close():
    l = [99.0] * N
    l[4] = 99.5
    p = _prep({"A": dict(c=[100.0] * N, l=l, entries=[2], stop=50.0)})
    cand = _cand(lambda sig, i, pos: 110.0 if i == 3 else pos.stop)
    (t,) = simulate(cand, p, _gate("A"), 0.04, 0, END).trades
    assert (t.exit_px, t.reason) == (100, "STOP")


def test_update_stop_cannot_lower_stop_or_see_future():
    l = [99.0] * N
    l[5] = 89
    p = _prep({"A": dict(c=[100.0] * N, l=l, entries=[2], stop=90.0)})
    lens = []

    def upd(sig, i, pos):
        lens.extend((i, len(v)) for v in sig.values())
        with pytest.raises(IndexError):
            sig["entry"][i + 1]
        pos.stop = 10.0
        return pos.stop

    (t,) = simulate(_cand(upd), p, _gate("A"), 0.04, 0, END).trades
    assert (t.exit_px, t.reason) == (90, "STOP")
    assert lens and all(n == i + 1 for i, n in lens)


def test_exit_cross_and_end_close():
    c = [100.0] * N
    c[4] = 105
    p = _prep({"A": dict(c=c, entries=[1], exit=[4]), "B": dict(c=[100.0] * N, entries=[1])})
    res = simulate(_cand(), p, _gate("A", "B"), 0.01, 0, END)
    a, b = sorted(res.trades, key=lambda t: t.symbol)
    assert (a.reason, a.exit_px, a.exit_ms) == ("CROSS_10_20", 105, 5 * HOUR_MS)
    assert (b.reason, b.exit_ms) == ("END", END)


def test_exposure_cap_skips_entry():
    spec = {s: dict(c=[100.0] * N, entries=[2], stop=98.4) for s in "AB"}
    res = simulate(_cand(), _prep(spec), _gate("A", "B"), 0.04, 0, END)
    assert [t.symbol for t in res.trades] == ["A"]


def test_entry_requires_gate_start_and_valid_stop():
    c = [100.0] * N
    p = _prep({"A": dict(c=c, entries=[5]),
               "B": dict(c=c, entries=[1]),
               "C": dict(c=c, entries=[5], stops={5: np.nan}),
               "D": dict(c=c, entries=[5], stops={5: 100.0})})
    res = simulate(_cand(), p, _gate("B", "C", "D"), 0.04, 3 * HOUR_MS, END)
    assert res.trades == []


def test_mtm_drawdown_counts_open_loss():
    c = [100.0] * N
    c[3], c[4] = 70, 110
    l = [99.0] * N
    l[3], l[4] = 70, 105
    p = _prep({"A": dict(c=c, l=l, entries=[2], stop=60.0, exit=[4])})
    res = simulate(_cand(), p, _gate("A"), 0.04, 0, END)
    assert res.max_dd == pytest.approx(0.03)
    assert res.trades[0].pnl > 0 and not res.bankrupt


def test_bankrupt_flag():
    l = [99.0] * N
    l[4] = 49
    p = _prep({"A": dict(c=[100.0] * N, l=l, entries=[2, 6], stop=50.0)})
    res = simulate(_cand(), p, _gate("A"), 2.0, 0, END)
    assert res.bankrupt and len(res.trades) == 1


def test_prepare_rejects_bad_candidate():
    fine = dict(ENTRY_TF="1h", SELECT_WINDOW_4H=10, TOP_N=1,
                select_score=lambda *a: None, btc_gate=lambda *a: True,
                update_stop=lambda *a: 0.0,
                signals=lambda b: {"entry": np.zeros(len(b), bool), "stop": np.zeros(len(b))})
    base = {"A": _bars(np.full(288, 100.0), step=300_000), "EMPTY": _empty()}
    p = prepare(SimpleNamespace(**fine), base, DAY_MS)
    assert list(p.bars) == ["A"] and len(p.bars["A"]) == 24 and p.tf_ms == HOUR_MS

    bad = [dict(ENTRY_TF="3h"),
           dict(signals=lambda b: {"entry": np.zeros(len(b), bool)}),
           dict(signals=lambda b: {"entry": np.zeros(len(b) - 1, bool), "stop": np.zeros(len(b))})]
    for over in bad:
        with pytest.raises(CandidateError):
            prepare(SimpleNamespace(**{**fine, **over}), base, DAY_MS)


def test_prepare_gives_candidate_readonly_bars():
    def sneaky(b):
        b.c[0] = 0
        return {}

    base = {"A": _bars(np.full(288, 100.0), step=300_000)}
    cand = SimpleNamespace(ENTRY_TF="5m", SELECT_WINDOW_4H=10, TOP_N=1, select_score=len, btc_gate=len,
                           update_stop=len, signals=sneaky)
    with pytest.raises((ValueError, CandidateError)):
        prepare(cand, base, DAY_MS)
    assert base["A"].c[0] == 100.0
