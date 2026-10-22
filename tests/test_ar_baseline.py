import importlib

import numpy as np
import pytest

from autoresearch.bars import DAY_MS, HOUR_MS, Bars
from autoresearch.baselines import variant_source
from strategy import shadow_lists as SL
from strategy import shotgun_v3 as V


def _walk(n, seed=0, drift=0.0005):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))


def _bars(c, step_ms, last_open):
    n = len(c)
    t = last_open - (n - 1 - np.arange(n, dtype=np.int64)) * step_ms
    return Bars(t, c, c, c, c, np.ones(n), np.ones(n))


@pytest.fixture
def cand():
    return importlib.import_module("autoresearch.baseline_candidate")


def _score(cand, mp, sel, w, cut):
    mp.setattr(cand, "SELECTION", sel)
    return cand.select_score(w, cut)


def test_selection_scores_match_live_functions(cand, monkeypatch):
    cut = 1_700_000_000_000 // DAY_MS * DAY_MS
    for seed in range(4):
        c = _walk(300, seed)
        w = _bars(c, 4 * HOUR_MS, cut - 4 * HOUR_MS)
        assert _score(cand, monkeypatch, "rs_v1", w, cut) == V.rs_score(c, 299)
        assert _score(cand, monkeypatch, "rs_v2", w, cut) == V.rs_score_v2(c, 299)
        for sel, r233 in (("alligator50", False), ("alligator233", True)):
            got, ref = _score(cand, monkeypatch, sel, w, cut), V.alligator_4h_score(c, 299, require_233=r233)
            assert (got is None) == (ref is None) and (ref is None or got == pytest.approx(ref, rel=1e-9))
    # 強上升趨勢，確保鱷魚與 martin30 有非 None 的分支
    c = 100 * np.exp(np.arange(300) * 0.004 + np.random.default_rng(1).normal(0, 0.002, 300))
    w = _bars(c, 4 * HOUR_MS, cut - 4 * HOUR_MS)
    for sel, r233 in (("alligator50", False), ("alligator233", True)):
        ref = V.alligator_4h_score(c, 299, require_233=r233)
        assert ref is not None and _score(cand, monkeypatch, sel, w, cut) == pytest.approx(ref, rel=1e-9)
    daily = w.c[w.t % DAY_MS == 20 * HOUR_MS]
    r30, ok = SL.martin30(daily)
    assert ok
    assert _score(cand, monkeypatch, "martin30", w, cut) == r30


def test_select_score_none_when_stale_or_short(cand, monkeypatch):
    cut = 1_700_000_000_000 // DAY_MS * DAY_MS
    c = _walk(300, 0)
    for sel in ("rs_v1", "rs_v2", "alligator50", "alligator233", "martin30"):
        assert _score(cand, monkeypatch, sel, _bars(c[:233], 4 * HOUR_MS, cut - 4 * HOUR_MS), cut) is None
    assert _score(cand, monkeypatch, "rs_v1", _bars(c[:234], 4 * HOUR_MS, cut - 4 * HOUR_MS), cut) is not None
    stale = _bars(c, 4 * HOUR_MS, cut - 8 * HOUR_MS)
    assert _score(cand, monkeypatch, "rs_v1", stale, cut) is not None
    for sel in ("rs_v2", "alligator50", "alligator233"):
        assert _score(cand, monkeypatch, sel, stale, cut) is None


def test_signals_entry_matches_entry_signal(cand):
    c = _walk(1500, 3, drift=0.001)
    sig = cand.signals(_bars(c, HOUR_MS, 1_700_000_000_000))
    ind = V.indicators(c)
    ref = [V.entry_signal(c[i], *(x[i] for x in ind)) for i in range(len(c))]
    assert sig["entry"].tolist() == ref and any(ref)
    assert np.allclose(sig["stop"], c * 0.8)


def test_btc_gate_needs_250_days(cand):
    assert cand.btc_gate(np.linspace(100, 200, 249)) is False
    assert cand.btc_gate(np.linspace(100, 200, 250)) is True


def test_variant_source_sets_selection():
    src = variant_source("martin30")
    assert 'SELECTION = "martin30"' in src
    assert any(l.startswith("DESCRIPTION") and "martin30" in l for l in src.splitlines())
    compile(src, "candidate.py", "exec")
