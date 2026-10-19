# Shotgun v3 Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bot's dual 15m Alligator strategy with Shotgun v3 (definitive recipe + BTC daily EMA200 switch + 4% notional + −20% disaster stop), trading logic proven identical to the core_backtests research.

**Architecture:** All trading rules live in one pure module (`strategy/shotgun_v3.py`, no I/O). A replay tool feeds historical data through that module and must reproduce the research trades 100%. A thin live layer (`strategy/shotgun_v3_live.py`) does REST I/O (daily RS list, BTC switch, warmup) and turns closed 1h futures klines into BUY/SELL decisions; `main.py` wires it to the existing PositionManager / testnet OrderExecutionService / Discord notifications.

**Tech Stack:** Python 3.13, pandas, numpy, python-binance (REST via `data/fetcher.CryptoFetcher`, already rate-limited), binance-connector 3.13 (`SpotWebsocketStreamClient` pointed at the futures stream URL), pytest.

**Spec:** `docs/shotgun-v3-bot-spec.md` (read it first). Research references live in the core_backtests repo (`C:\Users\jay\Desktop\core_backtests`): `research/rs_formula_variants.py` (`_scores`, variant `"skip"`), `strategies/common.py` (`rma`), `strategies/alligator_v4.py` (entry `long_sig`, `er`, `cross_event` exit), `research/shotgun_trend_parts.py` (`btc_daily_close`, `btc_bull_dates("ema")`), results `results/trend_parts_events.parquet` (arm `BASE_R`) and `results/rs_skip_gate.pkl`.

## Global Constraints

- All times UTC; a kline is labelled by its **open** time; the daily RS list and BTC switch computed at 00:00 UTC of day D apply to 1h bars whose open time is in day D.
- RS: 4h bars, 28 days = 168 bars, skip the last 6 bars (1 day), old formula `score = Σ w_j·j`, top 20 (ties: lower symbol first).
- BTC switch: BTC daily close of the just-closed day > `ewm(span=200, adjust=False)` of daily closes (≥1,000 days of history).
- Indicators on 1h closes: SMMA = Pine `ta.rma` seeded with the first full-window SMA, lengths 10/20/50/233; ER50 = (close − close[50]) / Σ|Δclose| over 50, 0 when undefined.
- Entry: flat, in day's list, bull day, SMMA10>20>50>233, close>SMMA10, ER50 ≥ 0.2 → market buy, notional = paper equity × `NOTIONAL_PCT` (0.04).
- Exit: SMMA10 goes from ≥ SMMA20 (previous bar) to < SMMA20 (this bar) → market sell all. Disaster stop: STOP_MARKET at entry × (1 − `DISASTER_STOP_PCT`) (0.20).
- Long only, one position per symbol, no pyramiding. Exclude `EXCLUDE_SYMBOLS` (default `BTCDOMUSDT`) and tokenized stocks/commodities (`EQUITY_LIKE_BASES`).
- Warmup: 1,500 closed 1h bars per symbol. REST only through `CryptoFetcher` (weight-limited).
- No new third-party dependencies.

## Review Focus

1. Websocket reconnect skips closed 1h bars → indicators silently drift. Expected: a gap in open times triggers a REST re-warmup before deciding (Task 3, `test_gap_triggers_rewarm`).
2. The 23:00 bar closes exactly at 00:00 when the new day's list is computed. Expected: it is judged with the **previous** day's list/switch (Task 3, `test_bar_uses_list_of_its_open_day`).
3. Exchange disaster stop fills, then the bot also sends a close. Expected: the close is `reduceOnly` so it can never open a short (Task 4, `test_close_position_is_reduce_only`).
4. Restart with open positions whose symbols are no longer in the list. Expected: they are still warmed up and subscribed so the exit still fires (Task 5, `test_bootstrap_subscribes_open_positions`).
5. The same closed kline delivered twice / out of order. Expected: ignored, indicators updated once (Task 1, `test_duplicate_kline_is_ignored`).

---

### Task 1: Pure strategy module

**Files:**
- Create: `strategy/shotgun_v3.py`
- Test: `tests/test_shotgun_v3.py`

**Interfaces:**
- Produces: constants `SMMA_LENS, ER_LEN, ER_MIN, RS_BARS_PER_DAY, RS_DAYS, RS_BARS, RS_TOP_N, BTC_EMA_SPAN, HOUR_MS`;
  `rma_seeded(values, n) -> np.ndarray`; `efficiency_ratio(close, n=ER_LEN) -> np.ndarray`;
  `indicators(close) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]` (s10, s20, s50, s233, er);
  `entry_signal(close, s10, s20, s50, s233, er) -> bool`; `exit_signal(prev_s10, prev_s20, s10, s20) -> bool`;
  `rs_score(close_4h, end) -> float | None`; `rank_top(scores: dict[str, float], top_n=RS_TOP_N) -> list[str]`;
  `btc_bull(daily_closes) -> bool`; `class GapError(Exception)`; `@dataclass Snapshot(close, s10, s20, s50, s233, er, prev_s10, prev_s20)`;
  `class SymbolState` with `SymbolState.from_history(open_ms, closes) -> SymbolState`, `.last_open_ms: int`, `.update(open_ms, close) -> Snapshot | None` (raises `GapError`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shotgun_v3.py
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
    assert V.exit_signal(10.0, 10.0, 9.9, 10.0)                        # 由 ≥ 轉 <
    assert not V.exit_signal(9.8, 10.0, 9.7, 10.0)                     # 原本就 <，不是事件


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shotgun_v3.py -q`
Expected: FAIL with `ImportError: cannot import name 'shotgun_v3'`

- [ ] **Step 3: Implement**

```python
# strategy/shotgun_v3.py
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


def exit_signal(prev_s10, prev_s20, s10, s20):
    return bool(prev_s10 >= prev_s20 and s10 < s20)


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shotgun_v3.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add strategy/shotgun_v3.py tests/test_shotgun_v3.py
git commit -m "feat: add Shotgun v3 pure strategy logic"
```

---

### Task 2: Replay parity against the research (gate before any live wiring)

**Files:**
- Create: `tools/replay_parity.py`

**Interfaces:**
- Consumes: Task 1 (`indicators`, `entry_signal`, `exit_signal`, `rs_score`, `btc_bull`).
- Produces: `replay_symbol(open_ms, opens, closes, gate_dates, bull_dates) -> list[tuple[int, int, float, float]]`
  (entry_open_ms, exit_open_ms, entry_px, exit_px); CLI `python tools/replay_parity.py --research-root <path> [--symbols 30] [--seed 0]`, exit code 0 only if all three checks are 100%.

- [ ] **Step 1: Write the tool**

```python
# tools/replay_parity.py
"""重播對帳：把 strategy/shotgun_v3.py 用在 core_backtests 的歷史資料上，必須與研究 100% 一致。

三項檢查：(1) RS 分數 vs 研究 rs_formula_variants._scores("skip")；(2) BTC 開關 vs 研究 btc_bull_dates("ema")；
(3) 抽 N 檔，交易（進出場時間與價格）vs 研究 results/trend_parts_events.parquet（arm = BASE_R）。
"""
import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import shotgun_v3 as V  # noqa: E402


def replay_symbol(open_ms, opens, closes, gate_dates, bull_dates):
    s10, s20, s50, s233, er = V.indicators(closes)
    days = pd.to_datetime(open_ms, unit="ms").date
    trades, pos = [], None
    for i in range(1, len(closes) - 1):
        if pos is not None and V.exit_signal(s10[i - 1], s20[i - 1], s10[i], s20[i]):
            trades.append((pos[0], int(open_ms[i + 1]), pos[1], float(opens[i + 1])))
            pos = None
            continue
        if pos is None and days[i] in gate_dates and days[i] in bull_dates and \
                V.entry_signal(closes[i], s10[i], s20[i], s50[i], s233[i], er[i]):
            pos = (int(open_ms[i + 1]), float(opens[i + 1]))
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--research-root", required=True)
    ap.add_argument("--symbols", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    root = Path(a.research_root)
    sys.path[:0] = [str(root / "research"), str(root)]
    import os
    os.chdir(root)                                   # 研究模組用相對路徑 data/、results/
    import rs_formula_variants as rfv
    import shotgun_trend_parts as T
    from rs_pipeline import _parquet_path

    ok = True
    ev = pd.read_parquet(root / "results/trend_parts_events.parquet")
    ev = ev[(ev["arm"] == "BASE_R") & (ev["symbol"] != "BTCDOM/USDT:USDT")]
    rng = np.random.default_rng(a.seed)
    syms = sorted(rng.choice(sorted(ev["symbol"].unique()), size=a.symbols, replace=False))

    # (1) RS 分數
    rfv._init_scores("skip")
    n_rs = bad_rs = 0
    for sym in syms:
        d = rfv._load(sym)
        ts = d["ts"].to_numpy(dtype="datetime64[ns]")
        c = d["close"].to_numpy(float)
        for cut, _, ref in rfv._scores(sym):
            end = int(np.searchsorted(ts, np.datetime64(cut), "left")) - 1
            got = V.rs_score(c, end)
            n_rs += 1
            bad_rs += not (got is not None and np.isclose(got, ref, rtol=1e-9))
    print(f"(1) RS 分數一致：{n_rs - bad_rs}/{n_rs}")
    ok &= bad_rs == 0

    # (2) BTC 開關
    dc = T.btc_daily_close()
    ref_bull = T.btc_bull_dates("ema")
    days = [d for d in dc.index if d >= pd.Timestamp("2021-01-01")]
    bad_b = sum(V.btc_bull(dc[dc.index < d].to_numpy()) != (d.date() in ref_bull) for d in days)
    print(f"(2) BTC 開關一致：{len(days) - bad_b}/{len(days)}")
    ok &= bad_b == 0

    # (3) 交易
    gate = pickle.loads((root / "results/rs_skip_gate.pkl").read_bytes())
    n_tr = bad_tr = 0
    for sym in syms:
        k = pd.read_parquet(_parquet_path(sym, "1h"))
        t = pd.to_datetime(k["ts"]).astype("datetime64[ns]").to_numpy().view("i8") // 1_000_000
        got = replay_symbol(t, k["open"].to_numpy(float), k["close"].to_numpy(float), gate.get(sym, set()), ref_bull)
        ref = ev[ev["symbol"] == sym]
        ref = list(zip((pd.to_datetime(ref["entry_time"]).astype("datetime64[ns]").to_numpy().view("i8") // 1_000_000).tolist(),
                       (pd.to_datetime(ref["exit_time"]).astype("datetime64[ns]").to_numpy().view("i8") // 1_000_000).tolist(),
                       ref["entry_px"].tolist(), ref["exit_px"].tolist()))
        n_tr += len(ref)
        same = len(got) == len(ref) and all(g[0] == r[0] and g[1] == r[1] and np.isclose(g[2], r[2]) and np.isclose(g[3], r[3])
                                            for g, r in zip(got, ref))
        if not same:
            bad_tr += len(ref)
            print(f"  ✗ {sym}: bot {len(got)} 筆 vs 研究 {len(ref)} 筆")
    print(f"(3) 交易一致：{n_tr - bad_tr}/{n_tr}（{len(syms)} 檔）")
    ok &= bad_tr == 0
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the research repo**

Run: `$env:PYTHONIOENCODING='utf-8'; C:\Users\jay\Desktop\core_backtests\venv\Scripts\python.exe tools/replay_parity.py --research-root C:\Users\jay\Desktop\core_backtests`
(Use the research venv: it has pyarrow and the research dependencies.)
Expected: three lines each `N/N`, then `PASS`, exit code 0. If anything is < 100%, stop and find the difference before continuing — do not proceed to Task 3.

- [ ] **Step 3: Commit**

```bash
git add tools/replay_parity.py
git commit -m "test: add replay parity tool against core_backtests research"
```

---

### Task 3: Live layer (REST list/switch, warmup, kline → decision) + config

**Files:**
- Create: `strategy/shotgun_v3_live.py`
- Modify: `config.py` (append Shotgun v3 block)
- Test: `tests/test_shotgun_v3_live.py`

**Interfaces:**
- Consumes: Task 1 module; `data.fetcher.CryptoFetcher` (`get_all_symbols() -> list[str]`, `fetch_klines(symbol, interval, limit) -> list[list]`, raw Binance kline rows `[open_ms, o, h, l, c, v, close_ms, ...]`).
- Produces: `class ShotgunV3Live(fetcher, exclude_symbols=(), exclude_bases=(), now_ms=None)` with
  `daily_rebalance(cutoff: pd.Timestamp) -> tuple[list[str], bool]`, `warmup(symbol) -> bool`,
  `on_closed_kline(symbol, open_ms, close, has_position) -> str | None` ("BUY"/"SELL"/None), `day_info(open_ms) -> tuple[frozenset, bool]`, `states: dict`.
  Config: `NOTIONAL_PCT, DISASTER_STOP_PCT, EXCLUDE_SYMBOLS, EQUITY_LIKE_BASES, POSITIONS_CSV_V3`.

- [ ] **Step 1: Append config**

```python
# config.py (append at the end, before nothing else is needed)
# --- Shotgun v3（docs/shotgun-v3-bot-spec.md）---
NOTIONAL_PCT = float(os.getenv("NOTIONAL_PCT", "0.04"))
DISASTER_STOP_PCT = float(os.getenv("DISASTER_STOP_PCT", "0.20"))
EXCLUDE_SYMBOLS = [s for s in os.getenv("EXCLUDE_SYMBOLS", "BTCDOMUSDT").split(",") if s]
POSITIONS_CSV_V3 = "data/positions_shotgun_v3.csv"
# 代幣化股票／商品（同研究 portfolio_backtest.EQUITY_LIKE）
EQUITY_LIKE_BASES = set("""SNDK SKHYNIX SOXL XAU XAG MU KORU DKNG UBER DIS TTWO SONY
AAPL TSLA NVDA MSFT AMZN GOOGL META NFLX AMD INTC COIN MSTR HOOD PLTR SPY QQQ IWM
GLD SLV TQQQ SOXX ARKK BABA NIO PYPL SQ SHOP ABNB RIVN LCID F GM BA JPM GS V MA
WMT COST PEP KO MCD NKE SBUX CRM ORCL ADBE AVGO QCOM TXN ASML TSM ARM SMCI DELL
HPQ IBM CSCO T VZ XOM CVX OXY PFE JNJ UNH LLY MRNA ABBV TMO DHR CAT DE MMM GE HON
UPS FDX LMT RTX NOC""".split())
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_shotgun_v3_live.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shotgun_v3_live.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'strategy.shotgun_v3_live'`

- [ ] **Step 4: Implement**

```python
# strategy/shotgun_v3_live.py
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

    def on_closed_kline(self, symbol, open_ms, close, has_position):
        st = self.states.get(symbol)
        if st is None:
            return None
        try:
            snap = st.update(open_ms, close)
        except V.GapError as e:
            logger.warning(f"{symbol}: {e} → re-warmup from REST")
            t, c = self._load_1h(symbol)                       # 只補抓一次，REST 已含漏掉的 K 棒
            if len(c) < V.SMMA_LENS[-1] + V.ER_LEN or t[-1] != open_ms:
                return None
            self.states[symbol] = V.SymbolState.from_history(t, c)
            s10, s20, s50, s233, er = V.indicators(c)
            snap = V.Snapshot(float(c[-1]), s10[-1], s20[-1], s50[-1], s233[-1], er[-1], s10[-2], s20[-2])
        if snap is None:
            return None
        if has_position:
            return "SELL" if V.exit_signal(snap.prev_s10, snap.prev_s20, snap.s10, snap.s20) else None
        lst, bull = self.day_info(open_ms)
        if symbol in lst and bull and V.entry_signal(snap.close, snap.s10, snap.s20, snap.s50, snap.s233, snap.er):
            return "BUY"
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shotgun_v3_live.py tests/test_shotgun_v3.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add config.py strategy/shotgun_v3_live.py tests/test_shotgun_v3_live.py
git commit -m "feat: add Shotgun v3 live layer and config"
```

---

### Task 4: Position manager notional sizing + signal exit; reduce-only closes

**Files:**
- Modify: `services/position_manager.py` (add methods after `update_positions`)
- Modify: `services/order_execution_service.py` (`place_market_order`, `_place_conditional_order`, `close_position`)
- Test: `tests/test_position_manager_v3.py`

**Interfaces:**
- Produces: `PositionManager.paper_equity() -> float`;
  `PositionManager.open_notional(symbol, price, timestamp, notional_pct, disaster_stop_pct) -> bool`;
  `PositionManager.close_by_signal(symbol, price, timestamp, reason="CROSS_10_20") -> bool`;
  `OrderExecutionService.place_market_order(symbol, side, quantity, reduce_only=False)`.
- Existing `update_positions()` keeps closing on `low <= stop_loss` (the paper mirror of the exchange disaster stop); `take_profit` is NaN for v3 rows so TP never fires.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_position_manager_v3.py
from unittest.mock import MagicMock

import pandas as pd

from services.order_execution_service import OrderExecutionService
from services.position_manager import PositionManager


def _pm(tmp_path, oes=None):
    notif = MagicMock()
    cfg = {"TOTAL_CAPITAL": 10000.0, "TRANSACTION_FEE_PERCENT": 0.0005}
    return PositionManager(notif, cfg, str(tmp_path / "p.csv"), order_execution_service=oes), notif


def test_open_notional_sizes_by_equity_and_sets_disaster_stop(tmp_path):
    pm, _ = _pm(tmp_path)
    assert pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    row = pm.positions_df.iloc[-1]
    assert row["units"] == 10000.0 * 0.04 / 2.0 and row["stop_loss"] == 2.0 * 0.8 and pd.isna(row["take_profit"])
    assert not pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)   # 同幣不重複


def test_close_by_signal_books_pnl_and_equity(tmp_path):
    pm, _ = _pm(tmp_path)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    assert pm.close_by_signal("AAAUSDT", 2.2, pd.Timestamp("2026-01-02"))
    pnl = 200 * 0.2 - 200 * 2.0 * 0.0005 - 200 * 2.2 * 0.0005
    assert abs(pm.positions_df.iloc[-1]["pnl"] - pnl) < 1e-9
    assert abs(pm.paper_equity() - (10000.0 + pnl)) < 1e-9
    assert pm.get_open_positions_symbols() == []


def test_paper_disaster_stop_via_update_positions(tmp_path):
    pm, _ = _pm(tmp_path)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    k = pd.Series({"Datetime": pd.Timestamp("2026-01-02"), "High": 2.0, "Low": 1.5, "Close": 1.7})
    pm.update_positions({"AAAUSDT": k})
    assert pm.positions_df.iloc[-1]["exit_reason"] == "STOP_LOSS"


def test_testnet_orders_entry_and_stop_only(tmp_path):
    oes = MagicMock()
    pm, _ = _pm(tmp_path, oes)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    oes.place_market_order.assert_called_once_with("AAAUSDT", "BUY", 200.0)
    oes.place_stop_loss_order.assert_called_once_with("AAAUSDT", "SELL", 200.0, 1.6)
    oes.place_take_profit_order.assert_not_called()


def test_close_position_is_reduce_only():
    svc = OrderExecutionService.__new__(OrderExecutionService)
    svc.client = MagicMock()
    svc._symbol_info_cache = {"AAAUSDT": {"quantityPrecision": 3, "pricePrecision": 4, "stepSize": 0.001, "tickSize": 0.0001}}
    svc.close_position("AAAUSDT", 200.0)
    kwargs = svc.client.futures_create_order.call_args.kwargs
    assert kwargs["reduceOnly"] == "true" and kwargs["side"] == "SELL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_position_manager_v3.py -q`
Expected: FAIL with `AttributeError: 'PositionManager' object has no attribute 'open_notional'`

- [ ] **Step 3: Implement — order service**

```python
# services/order_execution_service.py — replace place_market_order
    def place_market_order(self, symbol: str, side: str, quantity: float, reduce_only: bool = False) -> dict | None:
        """Place a market order on futures testnet. reduce_only=True can never open/flip a position."""
        qty = self._round_quantity(symbol, quantity)
        params = dict(symbol=symbol, side=side, type='MARKET', quantity=qty)
        if reduce_only:
            params["reduceOnly"] = "true"
        try:
            order = self.client.futures_create_order(**params)
            logger.info(f"Market {side} order placed: {symbol} qty={qty} reduceOnly={reduce_only} orderId={order.get('orderId')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place market {side} order for {symbol}: {e}")
            return None
```

```python
# services/order_execution_service.py — in _place_conditional_order, replace closePosition='false', with:
                reduceOnly='true',
```

```python
# services/order_execution_service.py — replace close_position
    def close_position(self, symbol: str, quantity: float) -> dict | None:
        """Close a position: cancel pending orders then reduce-only market sell
        (if the exchange stop already filled, this is rejected instead of opening a short)."""
        self.cancel_open_orders(symbol)
        return self.place_market_order(symbol, 'SELL', quantity, reduce_only=True)
```

- [ ] **Step 4: Implement — position manager (append to class)**

```python
    # --- Shotgun v3 ---
    def paper_equity(self) -> float:
        closed = self.positions_df[self.positions_df['status'] == 'CLOSED'] if not self.positions_df.empty else self.positions_df
        realized = float(pd.to_numeric(closed['pnl'], errors='coerce').fillna(0).sum()) if len(closed) else 0.0
        return float(self.strategy_config.get('TOTAL_CAPITAL')) + realized

    def open_notional(self, symbol, price, timestamp, notional_pct, disaster_stop_pct) -> bool:
        if symbol in self.get_open_positions_symbols():
            logger.info(f"Position for {symbol} is already open. Ignoring BUY.")
            return False
        units = self.paper_equity() * notional_pct / price
        stop = price * (1 - disaster_stop_pct)
        row = {'symbol': symbol, 'status': 'OPEN', 'entry_price': price, 'stop_loss': stop,
               'take_profit': float('nan'), 'units': units, 'entry_timestamp': timestamp,
               'exit_timestamp': pd.NaT, 'exit_reason': None, 'pnl': -self._opening_fee(price, units)}
        self.positions_df = pd.concat([self.positions_df, pd.DataFrame([row])], ignore_index=True)
        self._save_positions()
        orders = None
        if self.order_execution_service:
            try:
                entry = self.order_execution_service.place_market_order(symbol, 'BUY', units)
                sl = self.order_execution_service.place_stop_loss_order(symbol, 'SELL', units, stop) if entry else None
                orders = {'entry': entry, 'stop_loss': sl} if entry else None
            except Exception as e:
                logger.error(f"Testnet order execution error for {symbol}: {e}")
        self.notification_service.send_trade_notification(
            symbol=symbol, action="BUY", price=price, units=units, stop_loss_price=stop,
            is_volume_on=False, testnet_orders=orders)
        return True

    def close_by_signal(self, symbol, price, timestamp, reason="CROSS_10_20") -> bool:
        idx = self.positions_df[(self.positions_df['symbol'] == symbol) & (self.positions_df['status'] == 'OPEN')].index
        if idx.empty:
            return False
        i = idx[0]
        pos = self.positions_df.loc[i]
        fee = self.strategy_config.get('TRANSACTION_FEE_PERCENT')
        pnl = (price - pos['entry_price']) * pos['units'] - self._opening_fee(pos['entry_price'], pos['units']) \
            - price * pos['units'] * fee
        self.positions_df.loc[i, ['status', 'exit_reason', 'exit_timestamp', 'pnl']] = ['CLOSED', reason, timestamp, pnl]
        self._save_positions()
        orders = self._execute_close_orders(symbol, pos['units'])
        self.notification_service.send_trade_notification(
            symbol=symbol, action="SELL", price=price, units=pos['units'], reason=reason, pnl=pnl,
            is_volume_on=False, testnet_orders=orders)
        return True
```

Note: existing `update_positions()` already books `pnl = (exit − entry)·units − closing_fee` (opening fee was booked at open in `pnl` and is overwritten there). Keep it as-is for the paper disaster stop; it is the pre-existing convention.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_position_manager_v3.py tests/test_order_execution_service.py -q`
Expected: all passed (the existing order-execution tests must still pass; if one asserts `closePosition='false'`, update that single assertion to `reduceOnly='true'` and say so in the commit message).

- [ ] **Step 6: Commit**

```bash
git add services/position_manager.py services/order_execution_service.py tests/test_position_manager_v3.py tests/test_order_execution_service.py
git commit -m "feat: notional sizing, signal exits and reduce-only closes for Shotgun v3"
```

---

### Task 5: Futures websocket + main wiring

**Files:**
- Modify: `services/websocket_manager.py` (constructor `stream_url`, use it)
- Modify: `main.py` (replace `process_kline_message` and `run_app`; keep `_init_order_execution_service`, `main`)
- Replace: `tests/test_main.py` (old tests cover the removed 08:00/20:00 Alligator loop and hang; replace with v3 tests)

**Interfaces:**
- Consumes: Task 3 `ShotgunV3Live`, Task 4 `PositionManager.open_notional/close_by_signal`, config names from Task 3.
- Produces: `FUTURES_STREAM_URL = "wss://fstream.binance.com"`; `WebSocketManager(api_key, api_secret, notification_service, stream_url=FUTURES_STREAM_URL)`;
  `main.handle_kline(message, live, position_manager, cfg) -> str | None`; `main.next_cutoff(now: pd.Timestamp) -> pd.Timestamp`;
  `main.bootstrap(live, position_manager, now: pd.Timestamp) -> set[str]` (symbols to subscribe).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_main.py
from unittest.mock import MagicMock

import pandas as pd

import main
from services.websocket_manager import FUTURES_STREAM_URL, WebSocketManager


def _msg(symbol, open_ms, o=1.0, h=1.1, l=0.9, c=1.0, closed=True):
    return {"s": symbol, "k": {"t": open_ms, "o": str(o), "h": str(h), "l": str(l), "c": str(c), "x": closed}}


def test_next_cutoff_is_next_utc_midnight():
    assert main.next_cutoff(pd.Timestamp("2026-01-01 13:45")) == pd.Timestamp("2026-01-02")
    assert main.next_cutoff(pd.Timestamp("2026-01-02 00:00")) == pd.Timestamp("2026-01-03")


def test_handle_kline_routes_buy_and_sell():
    live, pm = MagicMock(), MagicMock()
    cfg = {"NOTIONAL_PCT": 0.04, "DISASTER_STOP_PCT": 0.2}
    pm.get_open_positions_symbols.return_value = []
    live.on_closed_kline.return_value = "BUY"
    assert main.handle_kline(_msg("AAAUSDT", 0, c=2.0), live, pm, cfg) == "BUY"
    pm.open_notional.assert_called_once()
    assert pm.open_notional.call_args.args[:2] == ("AAAUSDT", 2.0)
    pm.get_open_positions_symbols.return_value = ["AAAUSDT"]
    live.on_closed_kline.return_value = "SELL"
    assert main.handle_kline(_msg("AAAUSDT", 3_600_000, c=2.2), live, pm, cfg) == "SELL"
    pm.close_by_signal.assert_called_once()


def test_bootstrap_subscribes_open_positions():
    live, pm = MagicMock(), MagicMock()
    live.daily_rebalance.return_value = (["AAAUSDT"], True)
    pm.get_open_positions_symbols.return_value = ["ZZZUSDT"]
    syms = main.bootstrap(live, pm, pd.Timestamp("2026-01-01 05:00"))
    assert syms == {"AAAUSDT", "ZZZUSDT"}
    warmed = {c.args[0] for c in live.warmup.call_args_list}
    assert warmed == {"AAAUSDT", "ZZZUSDT"}
    assert live.daily_rebalance.call_args_list[-1].args[0] == pd.Timestamp("2026-01-01")


def test_websocket_defaults_to_futures_stream():
    ws = WebSocketManager("k", "s", MagicMock())
    assert ws.stream_url == FUTURES_STREAM_URL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py -q`
Expected: FAIL with `ImportError: cannot import name 'FUTURES_STREAM_URL'`

- [ ] **Step 3: Implement — websocket manager**

```python
# services/websocket_manager.py — add near imports
FUTURES_STREAM_URL = "wss://fstream.binance.com"     # USDⓈ-M 合約（現貨 client 指到合約網址，訊息格式相同）
```

```python
# services/websocket_manager.py — __init__ signature and body additions
    def __init__(self, api_key, api_secret, notification_service, stream_url=FUTURES_STREAM_URL):
        self.stream_url = stream_url
        # ...existing assignments unchanged...
```

```python
# services/websocket_manager.py — in _run, replace the client construction line
                self.ws_client = SpotWebsocketStreamClient(stream_url=self.stream_url, on_message=self._message_handler,
                                                           on_close=self._on_close, on_error=self._on_error)
```

- [ ] **Step 4: Implement — main.py (replace `process_kline_message` and `run_app`; keep imports used, add new ones)**

```python
# main.py — imports (replace the strategy/alligator imports)
from config import (
    get_strategy_config, BINANCE_API_KEY, BINANCE_API_SECRET,
    USE_TESTNET, BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET,
    NOTIONAL_PCT, DISASTER_STOP_PCT, EXCLUDE_SYMBOLS, EQUITY_LIKE_BASES, POSITIONS_CSV_V3,
)
from data.fetcher import CryptoFetcher
from strategy.shotgun_v3_live import ShotgunV3Live
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService
from services.order_execution_service import OrderExecutionService
```

```python
# main.py — new functions (replace process_kline_message and run_app)
def next_cutoff(now: pd.Timestamp) -> pd.Timestamp:
    return now.normalize() + pd.Timedelta(days=1)


def handle_kline(message, live, position_manager, cfg):
    """收完的 1h K 棒：先用低點檢查紙上災難止損，再交給策略判斷 BUY/SELL。"""
    k, symbol = message['k'], message['s']
    open_ms, close = int(k['t']), float(k['c'])
    ts = pd.to_datetime(open_ms, unit='ms') + pd.Timedelta(hours=1)
    if symbol in position_manager.get_open_positions_symbols():
        position_manager.update_positions({symbol: pd.Series({
            'Datetime': ts, 'High': float(k['h']), 'Low': float(k['l']), 'Close': close})})
    has_position = symbol in position_manager.get_open_positions_symbols()
    action = live.on_closed_kline(symbol, open_ms, close, has_position)
    if action == "SELL":
        position_manager.close_by_signal(symbol, close, ts, "CROSS_10_20")
    elif action == "BUY":
        position_manager.open_notional(symbol, close, ts, cfg["NOTIONAL_PCT"], cfg["DISASTER_STOP_PCT"])
    return action


def bootstrap(live, position_manager, now):
    """啟動：算今天（以及前一天，給 23:00 那根用）的名單與開關，暖機名單 ∪ 持倉。"""
    today = now.normalize()
    live.daily_rebalance(today - pd.Timedelta(days=1))
    top, _ = live.daily_rebalance(today)
    symbols = set(top) | set(position_manager.get_open_positions_symbols())
    for s in symbols:
        live.warmup(s)
    return symbols


def run_app(args):
    logging.info("Shotgun v3 starting...")
    strategy_config = get_strategy_config()
    webhook_on = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_ON")
    webhook_off = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_OFF")
    if not webhook_on or not webhook_off:
        logging.error("Error: Both DISCORD_WEBHOOK_URL_VOLUME_ON and DISCORD_WEBHOOK_URL_VOLUME_OFF must be configured.")
        sys.exit(1)
    notification_service = NotificationService(webhook_urls_map={
        "volume_on": webhook_on, "volume_off": webhook_off,
        "general_targets": strategy_config.get("DISCORD_WEBHOOK_URL_GENERAL_TARGETS"),
    })
    cfg = {"NOTIONAL_PCT": NOTIONAL_PCT, "DISASTER_STOP_PCT": DISASTER_STOP_PCT}
    position_manager = PositionManager(notification_service, strategy_config, POSITIONS_CSV_V3,
                                       order_execution_service=_init_order_execution_service())
    live = ShotgunV3Live(CryptoFetcher(), exclude_symbols=EXCLUDE_SYMBOLS, exclude_bases=EQUITY_LIKE_BASES)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    subscribed = bootstrap(live, position_manager, now)
    cutoff = next_cutoff(now)
    notification_service.send_list_change_notification(added=set(subscribed), removed=set(), webhook_type="general_targets")
    ws = WebSocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET, notification_service=notification_service)
    ws.start(symbols=sorted(subscribed), interval='1h')
    try:
        while True:
            now = pd.Timestamp.now(tz="UTC").tz_localize(None)
            if now >= cutoff:
                top, bull = live.daily_rebalance(cutoff)
                new = set(top) - subscribed
                for s in new:
                    live.warmup(s)
                if new:
                    ws.subscribe(sorted(new))
                    subscribed |= new
                notification_service.send_list_change_notification(
                    added=set(top), removed=set(), webhook_type="general_targets")
                logging.info(f"Daily rebalance {cutoff.date()} bull={bull} list={top}")
                cutoff = next_cutoff(now)
            message = ws.get_message(block=True, timeout=1)
            if message:
                try:
                    handle_kline(message, live, position_manager, cfg)
                except Exception as e:
                    logging.error(f"handle_kline failed for {message.get('s')}: {e}", exc_info=True)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        ws.stop()
        logging.info("Application stopped.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py tests/test_shotgun_v3.py tests/test_shotgun_v3_live.py tests/test_position_manager_v3.py tests/test_fetcher.py tests/test_relative_strength_fetch.py tests/test_order_execution_service.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add main.py services/websocket_manager.py tests/test_main.py
git commit -m "feat: wire Shotgun v3 into the bot on 1h futures klines"
```

---

### Task 6: Paper smoke run + runbook

**Files:**
- Create: `docs/shotgun-v3-runbook.md`

- [ ] **Step 1: Paper smoke run (no testnet, real public data)**

Run (PowerShell, repo root):
```
$env:USE_TESTNET='false'; $env:DISCORD_WEBHOOK_URL_VOLUME_ON='https://example.invalid/on'; $env:DISCORD_WEBHOOK_URL_VOLUME_OFF='https://example.invalid/off'
$p = Start-Process -PassThru -NoNewWindow .venv\Scripts\python.exe main.py; Start-Sleep 240; Stop-Process $p.Id
Select-String -Path application.log -Pattern 'Rebalance|bull=|subscribed|Traceback|ERROR' | Select-Object -First 20
```
Expected: two `Rebalance ... bull=... list=[...]` lines (yesterday, today) with 20 symbols, a websocket subscription line, no `Traceback`; Discord errors for the invalid webhook are expected. `x-mbx-used-weight-1m` never above 1,800 (the limiter logs a pause otherwise).

- [ ] **Step 2: Write the runbook**

```markdown
# Shotgun v3 運行手冊

## 上線前
1. 對帳：`core_backtests\venv\Scripts\python.exe tools/replay_parity.py --research-root C:\Users\jay\Desktop\core_backtests` → 必須 PASS。
2. `.env`：`USE_TESTNET=true`、`BINANCE_TESTNET_API_KEY/SECRET`、Discord webhooks；可調 `NOTIONAL_PCT`（預設 0.04）、`DISASTER_STOP_PCT`（0.20）、`EXCLUDE_SYMBOLS`（BTCDOMUSDT）。
3. 舊的 alligator 倉位檔（data/positions_volume_*.csv）不再使用；v3 寫 `data/positions_shotgun_v3.csv`。

## 啟動
`pm2 start ecosystem.config.js`（main.py 會自行計算當日名單，不需 `--fetch-now`）。

## testnet 期間（2–4 週）每日檢查
- 每天 00:00 UTC 後 Discord 會收到當日名單；BTC 在日線 EMA200 之下時不會開新倉（log 中 `bull=False`）。
- 每日抽查：把前一日的 1h 資料用 `tools/replay_parity.py` 的 `replay_symbol` 離線重算，與 `data/positions_shotgun_v3.csv` 的進出場比對。
- 交易所端每筆多單都應有一張 −20% 的 STOP_MARKET（reduceOnly）。

## 已知落差
市價滑價、資金費率未計；啟動暖機只用 1,500 根 1h，SMMA233 與研究的初值差 < 0.5%，極少數邊界訊號可能不同。
```

- [ ] **Step 3: Commit**

```bash
git add docs/shotgun-v3-runbook.md
git commit -m "docs: add Shotgun v3 runbook"
```
