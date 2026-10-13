import numpy as np
import pandas as pd

from strategy import shotgun_v3 as V
from strategy.shotgun_v3_live import ShotgunV3Live

H, D = V.HOUR_MS, 24 * V.HOUR_MS
T0 = int(pd.Timestamp("2026-01-01").value // 1_000_000)


def _rows(closes, start_ms, step_ms):
    return [[start_ms + i * step_ms, c, c * 1.01, c * 0.99, c, 1.0, start_ms + (i + 1) * step_ms - 1]
            for i, c in enumerate(closes)]


class FakeFetcher:
    def __init__(self):
        self.calls = []
        self.k1h = {}

    def get_all_symbols(self):
        return ["AAAUSDT", "BBBUSDT", "BTCDOMUSDT", "TSLAUSDT", "BTCUSDT"]

    def fetch_klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        if interval == "4h":
            trend = {"AAAUSDT": 0.002, "BBBUSDT": -0.002}.get(symbol, 0.0)
            return _rows(100 * np.exp(np.cumsum(np.full(300, trend))), T0 - 300 * 4 * H, 4 * H)
        if interval == "1d":
            return _rows(np.linspace(20000, 60000, 1500), T0 - 1500 * D, D)
        return self.k1h[symbol]


def _live(fetcher, now_ms=T0):
    return ShotgunV3Live(fetcher, exclude_symbols=["BTCDOMUSDT"], exclude_bases={"TSLA"}, now_ms=lambda: now_ms)


def test_daily_rebalance_excludes_and_ranks():
    f = FakeFetcher()
    top, bull = _live(f).daily_rebalance(pd.Timestamp(T0, unit="ms"))
    assert top[0] == "AAAUSDT" and "BTCDOMUSDT" not in top and "TSLAUSDT" not in top
    assert bull is True
    assert all(lim <= 1500 for _, _, lim in f.calls)


def test_bar_uses_list_of_its_open_day():
    live = _live(FakeFetcher())
    live.days[pd.Timestamp(T0 - D, unit="ms").date()] = (frozenset({"AAAUSDT"}), True)
    live.days[pd.Timestamp(T0, unit="ms").date()] = (frozenset(), False)
    assert live.day_info(T0 - H) == (frozenset({"AAAUSDT"}), True)     # 23:00 那根屬前一日
    assert live.day_info(T0) == (frozenset(), False)


def test_on_closed_kline_buy_then_sell():
    f = FakeFetcher()
    up = 100 * np.exp(np.cumsum(np.full(1500, 0.001)))
    f.k1h["AAAUSDT"] = _rows(up, T0 - 1500 * H, H)
    live = _live(f, now_ms=T0)
    live.days[pd.Timestamp(T0, unit="ms").date()] = (frozenset({"AAAUSDT"}), True)
    assert live.warmup("AAAUSDT")
    assert live.on_closed_kline("AAAUSDT", T0, float(up[-1] * 1.001), has_position=False) == "BUY"
    act, px, t = None, float(up[-1]), T0
    while act is None:                                                    # 持續下跌直到死叉
        t += H
        px *= 0.98
        act = live.on_closed_kline("AAAUSDT", t, px, has_position=True)
    assert act == "SELL"


def test_gap_triggers_rewarm():
    f = FakeFetcher()
    up = 100 * np.exp(np.cumsum(np.full(1500, 0.001)))
    f.k1h["AAAUSDT"] = _rows(up, T0 - 1500 * H, H)
    live = _live(f, now_ms=T0)
    live.warmup("AAAUSDT")
    n_calls = len(f.calls)
    f.k1h["AAAUSDT"] = _rows(np.r_[up[2:], up[-1], up[-1]], T0 - 1498 * H, H)  # REST 已含漏掉的那根
    live.now_ms = lambda: T0 + 2 * H
    live.on_closed_kline("AAAUSDT", T0 + H, float(up[-1]), has_position=False)
    assert len(f.calls) == n_calls + 1 and live.states["AAAUSDT"].last_open_ms == T0 + H


def test_exit_fires_even_if_cross_bar_was_missed():
    """I1：交叉發生在停機期間（重啟暖機後已是 SMMA10<SMMA20），持倉時下一根就要出場。"""
    f = FakeFetcher()
    up = 100 * np.exp(np.cumsum(np.full(1460, 0.001)))
    down = up[-1] * np.exp(np.cumsum(np.full(40, -0.01)))
    f.k1h["AAAUSDT"] = _rows(np.r_[up, down], T0 - 1500 * H, H)
    live = _live(f, now_ms=T0)
    assert live.warmup("AAAUSDT")
    st = live.states["AAAUSDT"]
    assert st.s[10] < st.s[20]                                  # 交叉已在停機期間發生
    assert live.on_closed_kline("AAAUSDT", T0, float(down[-1] * 0.99), has_position=True) == "SELL"


def test_failed_warmup_is_retried_on_next_kline():
    """I2：暖機失敗不得讓該幣整個 session 失效；下一根收完時要補暖機並照常判斷。"""
    f = FakeFetcher()
    up = 100 * np.exp(np.cumsum(np.full(1500, 0.001)))
    f.k1h["AAAUSDT"] = None                                     # REST 暫時失敗
    live = _live(f, now_ms=T0 + H)
    assert not live.warmup("AAAUSDT")
    f.k1h["AAAUSDT"] = _rows(up, T0 - 1499 * H, H)              # 恢復，且已含剛收完的 T0 那根
    live.days[pd.Timestamp(T0, unit="ms").date()] = (frozenset({"AAAUSDT"}), True)
    assert live.on_closed_kline("AAAUSDT", T0, float(up[-1]), has_position=False) == "BUY"
    assert live.states["AAAUSDT"].last_open_ms == T0


def test_daily_rebalance_refuses_incomplete_data():
    """I3：母體抓不到大半、或 BTC 日線不足 1000 根時，不得悄悄用殘缺資料決定整天。"""
    import pytest
    from strategy.shotgun_v3_live import RebalanceIncomplete

    class Flaky(FakeFetcher):
        def fetch_klines(self, symbol, interval, limit):
            if interval == "4h" and symbol != "AAAUSDT":
                return None
            return super().fetch_klines(symbol, interval, limit)

    with pytest.raises(RebalanceIncomplete):
        _live(Flaky()).daily_rebalance(pd.Timestamp(T0, unit="ms"))

    class ShortBtc(FakeFetcher):
        def fetch_klines(self, symbol, interval, limit):
            rows = super().fetch_klines(symbol, interval, limit)
            return rows[-500:] if interval == "1d" else rows

    with pytest.raises(RebalanceIncomplete):
        _live(ShortBtc()).daily_rebalance(pd.Timestamp(T0, unit="ms"))
