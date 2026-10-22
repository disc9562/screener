import math

import numpy as np

from autoresearch.bars import Bars, resample
from autoresearch.indicators import rma, rma_last
from strategy import shotgun_v3 as V


def _walk(n=1200, seed=0, drift=0.0005):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))


def _bars5m(n=24):
    o = np.arange(n, dtype=float)
    return Bars(np.arange(n, dtype=np.int64) * 300_000, o, o + 2, o - 1, o + 1,
                np.ones(n), np.full(n, 2.0))


def test_resample_aggregates_ohlcv():
    r = resample(_bars5m(), 60)
    assert r.t.tolist() == [0, 3_600_000]
    assert r.o.tolist() == [0, 12] and r.c.tolist() == [12, 24]
    assert r.h.tolist() == [13, 25] and r.l.tolist() == [-1, 11]
    assert r.v.tolist() == [12, 12] and r.qv.tolist() == [24, 24]


def test_resample_keeps_partial_group():
    b = _bars5m()
    keep = np.r_[0:3, 6:24]
    b = Bars(*(a[keep] for a in (b.t, b.o, b.h, b.l, b.c, b.v, b.qv)))
    r = resample(b, 60)
    assert len(r) == 2 and r.v[0] == 9


def test_rma_matches_rma_seeded():
    c = _walk()
    for n in (10, 20, 50, 233):
        assert np.allclose(rma(c, n), V.rma_seeded(c, n), rtol=1e-12, equal_nan=True)


def test_rma_last_matches_and_short_input():
    c = _walk()
    assert math.isclose(rma_last(c[:300], 233), V.rma_seeded(c[:300], 233)[-1], rel_tol=1e-10)
    assert math.isclose(rma_last(c[:233], 233), c[:233].mean(), rel_tol=1e-12)
    assert math.isnan(rma_last(c[:100], 233))


def test_resample_empty_returns_empty():
    assert len(resample(_bars5m(0), 60)) == 0
