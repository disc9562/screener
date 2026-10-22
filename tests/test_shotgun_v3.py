import numpy as np
import pandas as pd
import pytest

from strategy import shotgun_v3 as V


def _ref_rma(x, n):
    out = np.full(len(x), np.nan)
    out[n - 1] = np.mean(x[:n])
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def _walk(n=1200, seed=0, drift=0.0005):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))


def test_rma_seeded_matches_pine():
    c = _walk()
    for n in V.SMMA_LENS:
        got = V.rma_seeded(c, n)
        assert np.isnan(got[n - 2]) and np.array_equal(got[n - 1:], _ref_rma(c, n)[n - 1:])


def test_efficiency_ratio_matches_definition():
    c = _walk(300)
    er = V.efficiency_ratio(c)
    for i in (50, 120, 299):
        path = np.abs(np.diff(c[i - 50:i + 1])).sum()
        assert np.isclose(er[i], (c[i] - c[i - 50]) / path)
    assert er[49] == 0.0


def test_entry_and_exit_rules():
    assert V.entry_signal(11, 10.5, 10.4, 10.3, 10.0, 0.2)
    assert not V.entry_signal(11, 10.5, 10.4, 10.3, 10.0, 0.19)       # ER 不足
    assert not V.entry_signal(10.4, 10.5, 10.4, 10.3, 10.0, 0.5)      # 收盤不在 SMMA10 之上
    assert not V.entry_signal(11, 10.5, 10.4, 10.3, np.nan, 0.5)      # SMMA233 尚未暖機
    assert V.exit_signal(9.9, 10.0)                                    # SMMA10 < SMMA20 → 出場
    assert not V.exit_signal(10.1, 10.0)


def _ref_rs(close, end, bars=168, days=28, skip=6):
    """研究 rs_formula_variants._scores("skip") 的逐字版本。"""
    s = pd.Series(close)
    m30, m45, m60 = (s.rolling(n).mean().to_numpy() for n in (30, 45, 60))
    e = end - skip
    if e + 1 < bars + 60:
        return None
    sl = slice(e - bars + 1, e + 1)
    c, a30, a45, a60 = close[sl], m30[sl], m45[sl], m60[sl]
    j = np.arange(bars, dtype=float)
    w = (((c - a30) + (c - a45) + (c - a60)) * ((j * days / bars) + 1) + (a30 - a45) + (a30 - a60) + (a45 - a60)) / a60
    return float(np.sum(w * j))


def test_rs_score_matches_research_formula():
    c = _walk(500, seed=3)
    for end in (240, 300, 499):
        assert np.isclose(V.rs_score(c, end), _ref_rs(c, end), rtol=1e-12)
    assert V.rs_score(c, 200) is None                                  # 不足 168+60+6 根


def test_rank_top_breaks_ties_by_symbol():
    assert V.rank_top({"B": 1.0, "A": 1.0, "C": 3.0}, top_n=2) == ["C", "A"]


def test_btc_bull_uses_ema200_of_daily_closes():
    up = np.linspace(100, 200, 1000)
    assert V.btc_bull(up)
    assert not V.btc_bull(np.r_[up, np.full(5, 120.0)])


def test_streaming_state_equals_batch():
    c = _walk(1600)
    t = np.arange(len(c), dtype=np.int64) * V.HOUR_MS
    st = V.SymbolState.from_history(t[:1500], c[:1500])
    snaps = [st.update(int(t[i]), float(c[i])) for i in range(1500, 1600)]
    s10, s20, s50, s233, er = V.indicators(c)
    last = snaps[-1]
    assert (last.s10, last.s20, last.s50, last.s233) == (s10[-1], s20[-1], s50[-1], s233[-1])
    assert np.isclose(last.er, er[-1]) and (last.prev_s10, last.prev_s20) == (s10[-2], s20[-2])


def test_duplicate_kline_is_ignored():
    c = _walk(400)
    t = np.arange(len(c), dtype=np.int64) * V.HOUR_MS
    st = V.SymbolState.from_history(t, c)
    before = (st.s[10], st.last_open_ms)
    assert st.update(int(t[-1]), float(c[-1]) * 2) is None
    assert (st.s[10], st.last_open_ms) == before


def test_gap_raises():
    c = _walk(400)
    t = np.arange(len(c), dtype=np.int64) * V.HOUR_MS
    st = V.SymbolState.from_history(t, c)
    with pytest.raises(V.GapError):
        st.update(int(t[-1]) + 2 * V.HOUR_MS, 100.0)


def test_skip_day_intact_rejects_last_day_crash():
    """若最後 24 小時（被 skip 的 6 根 4h）發生閃崩或跌破 SMA60，skip_day_intact 與 rs_score_v2 必須拒絕。"""
    up = 100 * np.exp(np.cumsum(np.full(300, 0.003)))
    assert V.skip_day_intact(up, 299)
    assert V.rs_score_v2(up, 299) is not None

    crashed = up.copy()
    crashed[-3:] *= 0.75   # 最後 12 小時閃崩 -25%，但舊版 rs_score 因跳過最後 6 根而看不見
    assert V.rs_score(crashed, 299) == V.rs_score(up, 299)
    assert not V.skip_day_intact(crashed, 299)
    assert V.rs_score_v2(crashed, 299) is None


def test_rs_score_v2_rejects_below_sma60_or_negative_score():
    """rs_score_v2 保留原版動能分數，但拒絕跌破 4h SMA60 或負分的標的。"""
    steady = 100 * np.exp(np.cumsum(np.full(300, 0.0025)))
    assert np.isclose(V.rs_score_v2(steady, 299), V.rs_score(steady, 299))
    down = 100 * np.exp(np.cumsum(np.full(300, -0.002)))
    assert V.rs_score_v2(down, 299) is None


def test_avg_daily_quote_vol():
    rows_with_qv = [[i, 10, 11, 9, 10, 100, i + 1, 600_000.0] for i in range(42)]
    assert np.isclose(V.avg_daily_quote_vol(rows_with_qv, days=7), 3_600_000.0)


def test_alligator_4h_score_requires_alignment():
    up = 100 * np.exp(np.cumsum(np.full(300, 0.0025)))
    sc = V.alligator_4h_score(up, 299, require_233=True)
    assert sc is not None and sc > 0
    down = 100 * np.exp(np.cumsum(np.full(300, -0.002)))
    assert V.alligator_4h_score(down, 299, require_233=True) is None


def _rows4h(closes, cut_ms, qv_per_bar=1_000_000.0):
    """最後一根剛好在 cut_ms 收完的 4h K 線；每根成交額 qv_per_bar（一天 6 根）。"""
    n = len(closes)
    return [[cut_ms - (n - i) * 4 * V.HOUR_MS, c, c, c, c, 1.0, cut_ms - (n - 1 - i) * 4 * V.HOUR_MS - 1, qv_per_bar]
            for i, c in enumerate(closes)]


def test_breadth_sma_requires_fresh_long_liquid_series():
    cut = 1000 * 4 * V.HOUR_MS
    closes = np.linspace(100, 160, 121)
    rows = _rows4h(closes, cut)                                              # 每天 600 萬 U → 可交易
    assert np.isclose(V.breadth_sma(rows, cut), closes[-120:].mean())       # 最近 120 根 = 20 日均線
    assert V.breadth_sma(rows[:-1], cut) is None                              # 最後一根不是剛收完（停牌／斷線）
    assert V.breadth_sma(rows[-119:], cut) is None                            # 不足 120 根
    assert V.breadth_sma(_rows4h(closes, cut, qv_per_bar=500_000.0), cut) is None   # 每天 300 萬 U < 500 萬


def test_breadth_share_and_gate():
    smas = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0}
    prices = {"A": 11.0, "B": 9.0, "C": 10.5, "E": 50.0}                    # D 沒價格、E 不是可交易幣
    assert V.breadth(prices, smas) == 2 / 3
    assert V.breadth({}, smas) is None and V.breadth(prices, {}) is None
    assert V.gate_on(True, 0.5) and not V.gate_on(True, 0.49)
    assert not V.gate_on(False, 0.9)                                          # BTC 跌破 EMA200
    assert not V.gate_on(True, None)                                          # 寬度未知 → 不開新倉


