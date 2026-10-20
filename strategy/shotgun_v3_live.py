"""Shotgun v3 實盤層：每日名單／BTC 開關（REST）、暖機、收完的 1h K 棒 → BUY/SELL 判斷。"""
from concurrent.futures import ThreadPoolExecutor
import logging
import time

import numpy as np
import pandas as pd

from strategy import shadow_lists as SL
from strategy import shotgun_v3 as V

logger = logging.getLogger(__name__)


MIN_UNIVERSE_COVERAGE = 0.9          # 4h 資料抓到的幣 ≥ 母體 90% 才算完整
MIN_BTC_DAYS = 1000
DEFAULT_FETCH_WORKERS = 4


class RebalanceIncomplete(Exception):
    """換日資料不完整：不得用殘缺母體或殘缺 BTC 日線決定一整天。"""


def _closed(rows, now_ms):
    """去掉還沒收完的最後一根（close_time >= 現在）。"""
    return [r for r in rows if int(r[6]) < now_ms]


def _dedup_sorted_rows(rows, cut_ms):
    """只留開盤時間 < cut_ms 的 K 棒，依開盤時間去重（留最後一筆）並排序。"""
    by_t = {}
    for r in (rows or []):
        t = int(r[0])
        if t < cut_ms:
            by_t[t] = r
    return [by_t[t] for t in sorted(by_t)]


class ShotgunV3Live:
    def __init__(self, fetcher, exclude_symbols=(), exclude_bases=(), now_ms=None, shadow_fetcher=None,
                 fetch_workers=DEFAULT_FETCH_WORKERS, rs_mode="alligator", min_daily_quote_vol=0.0):
        self.fetcher = fetcher
        self.shadow_fetcher = shadow_fetcher or fetcher   # 影子記錄在背景執行緒，用獨立 client（限速額度仍共用）
        self.exclude_symbols = set(exclude_symbols)
        self.exclude_bases = set(exclude_bases)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.fetch_workers = max(1, int(fetch_workers))
        self.rs_mode = rs_mode
        self.min_daily_quote_vol = float(min_daily_quote_vol)
        self.days = {}                       # date -> (frozenset 名單, 是否多頭日)
        self.states = {}                     # symbol -> V.SymbolState
        self.rows_4h = {}                    # 最近一次換日抓到的 4h（影子記錄重用，不再多抓）
        self.universe = []                   # 最近一次換日的母體（含 4h 沒抓到的幣）

    def _is_excluded(self, sym: str) -> bool:
        if sym in self.exclude_symbols:
            return True
        base = sym[:-4] if sym.endswith("USDT") else sym
        if base in self.exclude_bases:
            return True
        if base.startswith("1000") and base[4:] in self.exclude_bases:
            return True
        return False

    def _universe(self):
        return [s for s in self.fetcher.get_all_symbols() if not self._is_excluded(s)]

    def _fetch_4h_map(self, universe):
        if self.fetch_workers <= 1 or len(universe) <= 1:
            return {sym: self.fetcher.fetch_klines(sym, "4h", 300) for sym in universe}
        with ThreadPoolExecutor(max_workers=self.fetch_workers) as pool:
            futs = {sym: pool.submit(self.fetcher.fetch_klines, sym, "4h", 300) for sym in universe}
            return {sym: futs[sym].result() for sym in universe}

    @staticmethod
    def _is_fresh_4h(rows, cut_ms):
        """確認最後一根 4h K 棒就是 cutoff 前剛收完的那根（未停牌／未斷線）。"""
        return bool(rows) and int(rows[-1][0]) == cut_ms - 4 * V.HOUR_MS

    def _score_symbol(self, rows, cut_ms):
        if not self._is_fresh_4h(rows, cut_ms):
            return None
        if self.min_daily_quote_vol > 0 and V.avg_daily_quote_vol(rows) < self.min_daily_quote_vol:
            return None
        if self.rs_mode == "martin30":
            _, _, _, _, daily_c = SL.resample(rows, 4, 24, cut_ms)
            r30, ok = SL.martin30(daily_c)
            return float(r30) if ok and np.isfinite(r30) else None
        closes = np.array([float(r[4]) for r in rows])
        end = len(closes) - 1
        if self.rs_mode == "alligator":
            return V.alligator_4h_score(closes, end, require_233=True)
        if self.rs_mode == "alligator50":
            return V.alligator_4h_score(closes, end, require_233=False)
        if self.rs_mode == "v2":
            return V.rs_score_v2(closes, end)
        return V.rs_score(closes, end)

    def daily_rebalance(self, cutoff):
        cut_ms = int(cutoff.value // 1_000_000)
        universe = self._universe()
        raw_map = self._fetch_4h_map(universe)
        scores, fetched, rows_4h = {}, 0, {}
        for sym in universe:
            raw = raw_map.get(sym)
            if raw:
                fetched += 1
            rows = _dedup_sorted_rows(raw, cut_ms)
            if rows:
                rows_4h[sym] = rows
                sc = self._score_symbol(rows, cut_ms)
                if sc is not None:
                    scores[sym] = sc
        if not universe or fetched < MIN_UNIVERSE_COVERAGE * len(universe):
            raise RebalanceIncomplete(f"4h data for {fetched}/{len(universe)} symbols")
        top = V.rank_top(scores)
        btc = _dedup_sorted_rows(self.fetcher.fetch_klines("BTCUSDT", "1d", 1500), cut_ms)
        if len(btc) < MIN_BTC_DAYS:
            raise RebalanceIncomplete(f"BTC daily bars {len(btc)} < {MIN_BTC_DAYS}")
        bull = V.btc_bull([float(r[4]) for r in btc])
        self.days[cutoff.date()] = (frozenset(top), bull)
        self.rows_4h, self.universe = rows_4h, list(universe)
        for d in sorted(self.days)[:-3]:
            del self.days[d]
        logger.info(f"Rebalance {cutoff.date()} (mode={self.rs_mode}): bull={bull} list={top}")
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
                    **{f"age{h}": f["age"][h] for h in SL.TFS},
                    ladder=f["ladder"], ladder_complete=f["ladder_complete"], r30=f["r30"],
                    martin_ok=f["martin_ok"], martin_status=f["martin_status"])

    def shadow_snapshot(self, cutoff, top, rows_4h, universe=None):
        """換日當下：全母體每個幣一列（RS 名次、RS_v2 名次、是否在 Martin30 名單、各時框斜率、狀態、資料年齡）。
        換日時 4h 沒抓到的幣也留一列（4h 以上標 fetch_failed），看得出誰漏掉。"""
        cut_ms = int(cutoff.value // 1_000_000)
        syms = sorted(set(universe or []) | set(rows_4h))
        feats = {sym: SL.features(self._shadow_fetch(sym, "1h", 200), rows_4h.get(sym), cut_ms) for sym in syms}
        martin = sorted((s for s, f in feats.items() if f["martin_ok"]), key=lambda s: (-feats[s]["r30"], s))[:20]
        v2_scores = {}
        for s in syms:
            r4 = _dedup_sorted_rows(rows_4h.get(s), cut_ms)
            if self._is_fresh_4h(r4, cut_ms):
                sc2 = V.rs_score_v2(np.array([float(r[4]) for r in r4]), len(r4) - 1)
                if sc2 is not None:
                    v2_scores[s] = sc2
        top_v2 = V.rank_top(v2_scores)
        rank_v2 = {s: i + 1 for i, s in enumerate(top_v2)}
        rank = {s: i + 1 for i, s in enumerate(top)}
        return [dict(date=str(cutoff.date()), symbol=s, in_rs=s in rank, rs_rank=rank.get(s),
                     in_rs_v2=s in rank_v2, rs_v2_rank=rank_v2.get(s), in_martin=s in martin, **self._flat(f))
                for s, f in sorted(feats.items())]

    def entry_features(self, symbol, now_ms):
        """進場當下（訊號根收盤）各時框 EMA20 斜率與狀態；4h 抓 300 根，與每日快照的歷史長度一致。"""
        f = SL.features(self._shadow_fetch(symbol, "1h", 200), self._shadow_fetch(symbol, "4h", 300), now_ms)
        return self._flat(f)
