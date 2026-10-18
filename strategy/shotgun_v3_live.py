"""Shotgun v3 實盤層：每日名單／BTC 開關（REST）、暖機、收完的 1h K 棒 → BUY/SELL 判斷。"""
import logging
import time

import numpy as np
import pandas as pd

from strategy import shadow_lists as SL
from strategy import shotgun_v3 as V

logger = logging.getLogger(__name__)


MIN_UNIVERSE_COVERAGE = 0.9          # 4h 資料抓到的幣 ≥ 母體 90% 才算完整
MIN_BTC_DAYS = 1000


class RebalanceIncomplete(Exception):
    """換日資料不完整：不得用殘缺母體或殘缺 BTC 日線決定一整天。"""


def _closed(rows, now_ms):
    """去掉還沒收完的最後一根（close_time >= 現在）。"""
    return [r for r in rows if int(r[6]) < now_ms]


class ShotgunV3Live:
    def __init__(self, fetcher, exclude_symbols=(), exclude_bases=(), now_ms=None, shadow_fetcher=None):
        self.fetcher = fetcher
        self.shadow_fetcher = shadow_fetcher or fetcher   # 影子記錄在背景執行緒，用獨立 client（限速額度仍共用）
        self.exclude_symbols = set(exclude_symbols)
        self.exclude_bases = set(exclude_bases)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.days = {}                       # date -> (frozenset 名單, 是否多頭日)
        self.states = {}                     # symbol -> V.SymbolState
        self.rows_4h = {}                    # 最近一次換日抓到的 4h（影子記錄重用，不再多抓）

    def _universe(self):
        return [s for s in self.fetcher.get_all_symbols()
                if s not in self.exclude_symbols and s[:-4] not in self.exclude_bases]

    def daily_rebalance(self, cutoff):
        cut_ms = int(cutoff.value // 1_000_000)
        universe = self._universe()
        scores, fetched, rows_4h = {}, 0, {}
        for sym in universe:
            raw = self.fetcher.fetch_klines(sym, "4h", 300)
            if raw:
                fetched += 1
            rows = [r for r in (raw or []) if int(r[0]) < cut_ms]
            if rows:
                rows_4h[sym] = rows
                sc = V.rs_score(np.array([float(r[4]) for r in rows]), len(rows) - 1)
                if sc is not None:
                    scores[sym] = sc
        if not universe or fetched < MIN_UNIVERSE_COVERAGE * len(universe):
            raise RebalanceIncomplete(f"4h data for {fetched}/{len(universe)} symbols")
        top = V.rank_top(scores)
        btc = [r for r in (self.fetcher.fetch_klines("BTCUSDT", "1d", 1500) or []) if int(r[0]) < cut_ms]
        if len(btc) < MIN_BTC_DAYS:
            raise RebalanceIncomplete(f"BTC daily bars {len(btc)} < {MIN_BTC_DAYS}")
        bull = V.btc_bull([float(r[4]) for r in btc])
        self.days[cutoff.date()] = (frozenset(top), bull)
        self.rows_4h = rows_4h
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

    # ---------- 影子記錄（只記錄、不影響交易；定義見 strategy/shadow_lists.py） ----------
    def _shadow_fetch(self, symbol, interval, limit):
        """抓取失敗回 None（記成 fetch_failed），不讓單一幣的錯誤中斷整批。"""
        try:
            return self.shadow_fetcher.fetch_klines(symbol, interval, limit)
        except Exception as e:
            logger.warning(f"shadow fetch {symbol} {interval} failed: {e}")
            return None

    @staticmethod
    def _flat(f):
        return dict(**{f"s{h}": f["slope"][h] for h in SL.TFS}, **{f"st{h}": f["status"][h] for h in SL.TFS},
                    ladder=f["ladder"], ladder_complete=f["ladder_complete"], r30=f["r30"],
                    martin_ok=f["martin_ok"], martin_status=f["martin_status"])

    def shadow_snapshot(self, cutoff, top, rows_4h):
        """換日當下：全母體每個幣一列（RS 名次、是否在 Martin30 名單、各時框斜率與狀態）。只有 4h 也沒抓到的幣不在母體裡。"""
        cut_ms = int(cutoff.value // 1_000_000)
        feats = {sym: SL.features(self._shadow_fetch(sym, "1h", 200), rows4, cut_ms) for sym, rows4 in rows_4h.items()}
        martin = sorted((s for s, f in feats.items() if f["martin_ok"]), key=lambda s: (-feats[s]["r30"], s))[:20]
        rank = {s: i + 1 for i, s in enumerate(top)}
        return [dict(date=str(cutoff.date()), symbol=s, in_rs=s in rank, rs_rank=rank.get(s), in_martin=s in martin, **self._flat(f))
                for s, f in sorted(feats.items())]

    def entry_features(self, symbol, now_ms):
        """進場當下（訊號根收盤）各時框 EMA20 斜率與狀態；4h 抓 300 根，與每日快照的歷史長度一致。"""
        f = SL.features(self._shadow_fetch(symbol, "1h", 200), self._shadow_fetch(symbol, "4h", 300), now_ms)
        return self._flat(f)
