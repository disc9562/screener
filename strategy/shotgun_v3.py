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


def rs_score(close_4h, end):
    """end = 開盤時間 < 當日 00:00 的最後一根 4h 索引；跳過 6 根後取 168 根，舊公式 Σ w_j·j。"""
    e = end - RS_BARS_PER_DAY
    if e + 1 < RS_BARS + 60:
        return None
    s = pd.Series(np.asarray(close_4h[:e + 1], float))
    m30, m45, m60 = (s.rolling(n).mean().to_numpy() for n in (30, 45, 60))
    sl = slice(e - RS_BARS + 1, e + 1)
    c, a30, a45, a60 = s.to_numpy()[sl], m30[sl], m45[sl], m60[sl]
    j = np.arange(RS_BARS, dtype=float)
    w = (((c - a30) + (c - a45) + (c - a60)) * ((j * RS_DAYS / RS_BARS) + 1)
         + (a30 - a45) + (a30 - a60) + (a45 - a60)) / a60
    score = float(np.sum(w * j))
    return score if score != 0 and np.isfinite(score) else None


def rank_top(scores, top_n=RS_TOP_N):
    return [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]]


def btc_bull(daily_closes):
    c = pd.Series(np.asarray(daily_closes, float))
    return bool(c.iloc[-1] > c.ewm(span=BTC_EMA_SPAN, adjust=False).mean().iloc[-1])


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
