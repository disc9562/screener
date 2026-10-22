"""Shotgun v3 純策略邏輯（不碰 I/O）。必須與 core_backtests 研究逐筆一致。

規格：docs/shotgun-v3-bot-spec.md。研究對應：rs_formula_variants._scores("skip")、strategies/common.rma、
alligator_v4（long_sig、er、cross_event 出場）、shotgun_trend_parts.btc_bull_dates("ema")。
"""
from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

SMMA_LENS = (10, 20, 50, 233)
ER_LEN, ER_MIN = 50, 0.2
RS_BARS_PER_DAY, RS_DAYS, RS_TOP_N = 6, 28, 20
RS_BARS = RS_BARS_PER_DAY * RS_DAYS
BTC_EMA_SPAN = 200
HOUR_MS = 3_600_000
# 大盤寬度（docs/shotgun-v3.1-gate.md）：流動幣中，價格站上自己 20 日均線（120 根 4h）的比例
BREADTH_SMA_BARS = 120
BREADTH_MIN_DAILY_QV = 5_000_000
BREADTH_ON = 0.5


def rma_seeded(values, n):
    """Pine ta.rma：第一個值 = 前 n 根 SMA，之後 (前值·(n−1) + 本根) / n。"""
    v = np.asarray(values, float)
    out = np.full(len(v), np.nan)
    if len(v) < n:
        return out
    out[n - 1] = v[:n].mean()
    for i in range(n, len(v)):
        out[i] = (out[i - 1] * (n - 1) + v[i]) / n
    return out


def efficiency_ratio(close, n=ER_LEN):
    c = np.asarray(close, float)
    out = np.zeros(len(c))
    if len(c) > n:
        path = np.convolve(np.abs(np.diff(c)), np.ones(n), "valid")
        disp = c[n:] - c[:-n]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[n:] = np.where(path > 0, disp / path, 0.0)
    return out


def indicators(close):
    s10, s20, s50, s233 = (rma_seeded(close, n) for n in SMMA_LENS)
    return s10, s20, s50, s233, efficiency_ratio(close)


def entry_signal(close, s10, s20, s50, s233, er):
    return bool(np.isfinite(s233) and s10 > s20 > s50 > s233 and close > s10 and er >= ER_MIN)


def exit_signal(s10, s20):
    """持倉中 SMMA10 < SMMA20 就出場。進場要求 SMMA10 > SMMA20，所以連續處理時「持倉後第一根 <」
    就是研究的下穿事件；用狀態判斷則在漏掉交叉那根（停機、斷線）時仍會出場。"""
    return bool(s10 < s20)


def _sma_tail(arr, window, length):
    """計算 arr 末端 length 個位置的簡單移動平均（window 期），純 NumPy 避免 pandas 物件開銷。"""
    sub = arr[-(length + window - 1):]
    cs = np.empty(len(sub) + 1, dtype=float)
    cs[0] = 0.0
    np.cumsum(sub, out=cs[1:])
    return (cs[window:] - cs[:-window]) / window


def rs_score(close_4h, end):
    """end = 開盤時間 < 當日 00:00 的最後一根 4h 索引；跳過 6 根後取 168 根，舊公式 Σ w_j·j。"""
    e = end - RS_BARS_PER_DAY
    if e + 1 < RS_BARS + 60:
        return None
    arr = np.asarray(close_4h[:e + 1], dtype=float)
    c = arr[-RS_BARS:]
    a30 = _sma_tail(arr, 30, RS_BARS)
    a45 = _sma_tail(arr, 45, RS_BARS)
    a60 = _sma_tail(arr, 60, RS_BARS)
    j = np.arange(RS_BARS, dtype=float)
    w = (((c - a30) + (c - a45) + (c - a60)) * ((j * RS_DAYS / RS_BARS) + 1)
         + (a30 - a45) + (a30 - a60) + (a45 - a60)) / a60
    score = float(np.sum(w * j))
    return score if score != 0 and np.isfinite(score) else None


def skip_day_intact(close_4h, end, max_skip_drop=0.15):
    """檢查被跳過的最後 1 天（6 根 4h）結構是否完好：
    1. 最新收盤價 close[end] 仍須在 4h SMA60 之上（未破趨勢生命線）
    2. 最後 6 根內從高點回落不得超過 max_skip_drop（預設 15%，排除閃崩倒貨標的）"""
    if end + 1 < 60 or end < RS_BARS_PER_DAY:
        return False
    arr = np.asarray(close_4h[:end + 1], dtype=float)
    sma60_end = float(arr[-60:].mean())
    if not (np.isfinite(sma60_end) and arr[-1] > sma60_end):
        return False
    recent_window = arr[-(RS_BARS_PER_DAY + 1):]
    peak = float(np.max(recent_window))
    if peak <= 0 or (arr[-1] / peak - 1.0) < -max_skip_drop:
        return False
    return True


def rs_score_v2(close_4h, end, max_skip_drop=0.12):
    """優化版 RS v2（經歷史回測驗證）：
    保留原版 RS v1 動能加權公式（容許算分點 e 處於整理蓄勢狀態），
    並加上 skip_day_intact 破位保護（排除在被跳過的最後 24h 內閃崩超過 12% 或跌破 4h SMA60 的破位幣）。"""
    if not skip_day_intact(close_4h, end, max_skip_drop=max_skip_drop):
        return None
    return rs_score(close_4h, end)


def alligator_4h_score(close_4h, end, require_233=True):
    """4h 鱷魚線（SMMA 10/20/50[/233]）多頭開口選股指標：
    要求換日當下 close > SMMA10 > SMMA20 > SMMA50（若 require_233=True 則需 > SMMA233），
    並以三線相對 SMMA50 的開口幅度排序。"""
    min_bars = 250 if require_233 else 100
    if end + 1 < min_bars:
        return None
    arr = np.asarray(close_4h[:end + 1], dtype=float)
    s10 = float(rma_seeded(arr, 10)[-1])
    s20 = float(rma_seeded(arr, 20)[-1])
    s50 = float(rma_seeded(arr, 50)[-1])
    c = float(arr[-1])
    if not (np.isfinite(s50) and s50 > 0 and c > s10 > s20 > s50):
        return None
    if require_233:
        s233 = float(rma_seeded(arr, 233)[-1])
        if not (np.isfinite(s233) and s50 > s233):
            return None
    score = float(((c - s10) + (s10 - s20) + (s20 - s50)) / s50)
    return score if np.isfinite(score) and score > 0 else None


def avg_daily_quote_vol(rows_4h, days=7):
    """由 4h K 線計算最近 days 天（預設 7 天 = 42 根 4h）的日均 USDT 成交額。
    標準幣安 K 線第 7 欄為 quote_volume；若測試假資料無第 7 欄則以 close * volume 估算。"""
    n_bars = days * RS_BARS_PER_DAY
    recent = (rows_4h or [])[-n_bars:]
    if not recent:
        return 0.0
    total = sum(float(r[7]) if len(r) > 7 else float(r[4]) * float(r[5]) for r in recent)
    actual_days = max(len(recent) / RS_BARS_PER_DAY, 1.0)
    return float(total / actual_days)


def rank_top(scores, top_n=RS_TOP_N):
    return [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]]


def btc_bull(daily_closes):
    c = pd.Series(np.asarray(daily_closes, float))
    return bool(c.iloc[-1] > c.ewm(span=BTC_EMA_SPAN, adjust=False).mean().iloc[-1])


def breadth_sma(rows_4h, cut_ms, sma_bars=BREADTH_SMA_BARS, min_daily_qv=BREADTH_MIN_DAILY_QV):
    """單一幣的寬度基準：cut_ms 前收完的最近 sma_bars 根 4h 收盤平均（20 日均線）。
    rows_4h 須已依開盤時間排序去重、只含 cut_ms 前的 K 棒。最後一根不是剛在 cut_ms 收完（停牌／斷線）、
    根數不足、或 7 日日均成交額 < min_daily_qv（不算可交易幣）→ None。"""
    rows = rows_4h or []
    if len(rows) < sma_bars or int(rows[-1][0]) != cut_ms - 4 * HOUR_MS:
        return None
    if avg_daily_quote_vol(rows) < min_daily_qv:
        return None
    return float(np.mean([float(r[4]) for r in rows[-sma_bars:]]))


def breadth(prices, smas):
    """寬度 = 有基準又有價格的幣中，價格 > 20 日均線的比例；一個都沒有 → None。"""
    pairs = [(prices[s], m) for s, m in smas.items() if s in prices and np.isfinite(prices[s])]
    if not pairs:
        return None
    return sum(p > m for p, m in pairs) / len(pairs)


def gate_on(bull, breadth_value, breadth_on=BREADTH_ON):
    """新倉開關：BTC 日線在 EMA200 之上，且寬度 ≥ breadth_on。寬度未知 → 關（不進新倉）。"""
    return bool(bull) and breadth_value is not None and breadth_value >= breadth_on


class GapError(Exception):
    """收到的 K 棒不是上一根的下一根（斷線漏棒）→ 需要重新暖機。"""


@dataclass
class Snapshot:
    close: float
    s10: float
    s20: float
    s50: float
    s233: float
    er: float
    prev_s10: float
    prev_s20: float


class SymbolState:
    def __init__(self, s, closes, last_open_ms):
        self.s = s                                  # {n: 最新 SMMA}
        self.closes = closes                        # 最近 ER_LEN+1 根收盤
        self.last_open_ms = last_open_ms

    @classmethod
    def from_history(cls, open_ms, closes):
        closes = np.asarray(closes, float)
        arrs = indicators(closes)
        s = {n: float(a[-1]) for n, a in zip(SMMA_LENS, arrs[:4])}
        return cls(s, deque(closes[-(ER_LEN + 1):], maxlen=ER_LEN + 1), int(open_ms[-1]))

    def update(self, open_ms, close):
        if open_ms <= self.last_open_ms:
            return None
        if open_ms != self.last_open_ms + HOUR_MS:
            raise GapError(f"expected {self.last_open_ms + HOUR_MS}, got {open_ms}")
        prev10, prev20 = self.s[10], self.s[20]
        for n in SMMA_LENS:
            self.s[n] = (self.s[n] * (n - 1) + close) / n
        self.closes.append(close)
        c = np.asarray(self.closes)
        path = np.abs(np.diff(c)).sum()
        er = (c[-1] - c[0]) / path if len(c) == ER_LEN + 1 and path > 0 else 0.0
        self.last_open_ms = open_ms
        return Snapshot(close, self.s[10], self.s[20], self.s[50], self.s[233], er, prev10, prev20)
