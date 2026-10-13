"""Shotgun v3 實盤層：每日名單／BTC 開關（REST）、暖機、收完的 1h K 棒 → BUY/SELL 判斷。"""
import logging
import time

import numpy as np
import pandas as pd

from strategy import shotgun_v3 as V

logger = logging.getLogger(__name__)


def _closed(rows, now_ms):
    """去掉還沒收完的最後一根（close_time >= 現在）。"""
    return [r for r in rows if int(r[6]) < now_ms]


class ShotgunV3Live:
    def __init__(self, fetcher, exclude_symbols=(), exclude_bases=(), now_ms=None):
        self.fetcher = fetcher
        self.exclude_symbols = set(exclude_symbols)
        self.exclude_bases = set(exclude_bases)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.days = {}                       # date -> (frozenset 名單, 是否多頭日)
        self.states = {}                     # symbol -> V.SymbolState

    def _universe(self):
        return [s for s in self.fetcher.get_all_symbols()
                if s not in self.exclude_symbols and s[:-4] not in self.exclude_bases]

    def daily_rebalance(self, cutoff):
        cut_ms = int(cutoff.value // 1_000_000)
        scores = {}
        for sym in self._universe():
            rows = [r for r in (self.fetcher.fetch_klines(sym, "4h", 300) or []) if int(r[0]) < cut_ms]
            if rows:
                sc = V.rs_score(np.array([float(r[4]) for r in rows]), len(rows) - 1)
                if sc is not None:
                    scores[sym] = sc
        top = V.rank_top(scores)
        btc = [r for r in (self.fetcher.fetch_klines("BTCUSDT", "1d", 1500) or []) if int(r[0]) < cut_ms]
        bull = V.btc_bull([float(r[4]) for r in btc]) if len(btc) >= 1000 else False
        self.days[cutoff.date()] = (frozenset(top), bull)
        for d in sorted(self.days)[:-3]:
            del self.days[d]
        logger.info(f"Rebalance {cutoff.date()}: bull={bull} list={top}")
        return top, bull

    def day_info(self, open_ms):
        return self.days.get(pd.Timestamp(open_ms, unit="ms").date(), (frozenset(), False))

    def _load_1h(self, symbol):
        rows = _closed(self.fetcher.fetch_klines(symbol, "1h", 1500) or [], self.now_ms())
        return np.array([int(r[0]) for r in rows], dtype=np.int64), np.array([float(r[4]) for r in rows])

    def warmup(self, symbol):
        t, c = self._load_1h(symbol)
        if len(c) < V.SMMA_LENS[-1] + V.ER_LEN:
            logger.warning(f"Warmup {symbol}: only {len(c)} closed bars")
            return False
        self.states[symbol] = V.SymbolState.from_history(t, c)
        return True

    def _rewarm_snapshot(self, symbol, open_ms):
        """從 REST 重建狀態（只抓一次，REST 已含剛收完的這根），回傳這根的 Snapshot；資料不對齊回 None。"""
        t, c = self._load_1h(symbol)
        if len(c) < V.SMMA_LENS[-1] + V.ER_LEN or t[-1] != open_ms:
            return None
        self.states[symbol] = V.SymbolState.from_history(t, c)
        s10, s20, s50, s233, er = V.indicators(c)
        return V.Snapshot(float(c[-1]), s10[-1], s20[-1], s50[-1], s233[-1], er[-1], s10[-2], s20[-2])

    def on_closed_kline(self, symbol, open_ms, close, has_position):
        st = self.states.get(symbol)
        if st is None:                                         # 之前暖機失敗：這根收完時補一次
            logger.warning(f"{symbol}: no state → warmup from REST")
            snap = self._rewarm_snapshot(symbol, open_ms)
        else:
            try:
                snap = st.update(open_ms, close)
            except V.GapError as e:
                logger.warning(f"{symbol}: {e} → re-warmup from REST")
                snap = self._rewarm_snapshot(symbol, open_ms)
        if snap is None:
            return None
        if has_position:
            return "SELL" if V.exit_signal(snap.s10, snap.s20) else None
        lst, bull = self.day_info(open_ms)
        if symbol in lst and bull and V.entry_signal(snap.close, snap.s10, snap.s20, snap.s50, snap.s233, snap.er):
            return "BUY"
        return None
