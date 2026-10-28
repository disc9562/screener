"""端到端重播：把歷史 1h K 線依時間逐小時餵給 bot 本體，檢查 v3.1 的實際行為（docs/shotgun-v3.1-gate.md）。

走的是實盤同一套程式：ShotgunV3Live（換日名單、BTC 開關、寬度、1h 進出場）、main.hourly_gate／enforce_gate、
main.handle_kline、PositionManager（紙上模式）。順序照 main.run_app：整點先換日（00:00）→ 更新寬度與開關 →
再處理該整點收完的 K 棒（訂閱的是換日前的「名單 ∪ 持倉」）。資料只給「模擬當下已收完」的 K 棒。

資料夾格式（研究端 download_hist.py 下載的格式；資料不在這個 repo）：
  <data>/1h/<SYMBOL>.pkl = {"df": DataFrame[open_time, open, high, low, close, quote_volume, ...]}
  <data>/btc_1d.json     = Binance BTCUSDT 永續日線（起點前至少 1000 天）
4h 由 1h 合成（只用 4 根齊全的）。母體 = 模擬當下有資料的幣（倖存者偏差同研究）。

用法：
  python tools/replay_v31.py --data <K 線快取資料夾> --start 2025-08-01 --end 2026-02-01
  python tools/replay_v31.py ... --preset v3         # 原版開關（只看 BTC、不全平），10 格 × 10%
  python tools/replay_v31.py ... --preset v3-live    # 原版實盤設定：不限檔數 × 20%
  python tools/replay_v31.py ... --preset v3.1-live  # 原版倉位＋新開關
下單權益一律用本版的 sizing_equity（已實現＋浮虧）；原版只用已實現權益，研究模擬兩者在不限檔數時差 1%（8.8 vs 8.9 倍）。
輸出 <out>/trades.csv（每筆進出場）、<out>/equity.csv（逐小時權益），並印出摘要。
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

for _h in list(logging.getLogger().handlers):              # main 匯入時會掛 application.log；重播不寫實盤日誌
    logging.getLogger().removeHandler(_h)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
from config import EXCLUDE_SYMBOLS, MIN_DAILY_QUOTE_VOL, NON_CRYPTO_BASES, RS_MODE  # noqa: E402
from services.position_manager import PositionManager  # noqa: E402
from strategy import shotgun_v3 as V  # noqa: E402
from strategy.shotgun_v3_live import ShotgunV3Live  # noqa: E402

H = V.HOUR_MS
D = 24 * H
PRESETS = {
    "v3.1": dict(breadth_on=0.5, kill=True, max_positions=10, notional=0.10),
    "v3": dict(breadth_on=0.0, kill=False, max_positions=10, notional=0.10),
    "v3-live": dict(breadth_on=0.0, kill=False, max_positions=None, notional=0.20),
    "v3.1-live": dict(breadth_on=0.5, kill=True, max_positions=None, notional=0.20),   # 原版倉位＋新開關
}


def _ms(text):
    return int(pd.Timestamp(text).value // 1_000_000)


class ReplayFetcher:
    """CryptoFetcher 的替身：只回傳 now 之前已收完的 K 棒；價格 = 剛收完那根的收盤。"""

    def __init__(self, data_dir: Path, start_ms: int, end_ms: int, warmup_days: int = 75, btc_path: Path | None = None):
        self.now = start_ms
        self.k1h, self.k4h = {}, {}
        t0 = start_ms - warmup_days * D
        for p in sorted((data_dir / "1h").glob("*.pkl")):
            with open(p, "rb") as f:
                df = pickle.load(f)["df"]
            t = df["open_time"].to_numpy(np.int64)
            m = (t >= t0) & (t < end_ms)
            if m.sum() < 48:
                continue
            cols = {k: pd.to_numeric(df[k], errors="coerce").to_numpy(float)[m]
                    for k in ("open", "high", "low", "close", "quote_volume")}
            self.k1h[p.stem] = (t[m], cols)
            self.k4h[p.stem] = self._to_4h(t[m], cols)
        rows = json.loads((btc_path or data_dir / "btc_1d.json").read_text())
        self.btc_1d = (np.array([int(r[0]) for r in rows], np.int64),
                       {k: np.array([float(r[i]) for r in rows]) for k, i in
                        (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("quote_volume", 7))})

    @staticmethod
    def _to_4h(t, c):
        df = pd.DataFrame({"t": t, **c})
        df["b"] = df["t"] // (4 * H) * (4 * H)
        g = df.groupby("b").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                quote_volume=("quote_volume", "sum"), n=("t", "size"))
        g = g[g["n"] == 4]
        return g.index.to_numpy(np.int64), {k: g[k].to_numpy(float) for k in ("open", "high", "low", "close", "quote_volume")}

    def _rows(self, series, step, limit):
        t, c = series
        k = int(np.searchsorted(t, self.now - step, side="right"))        # 已收完：開盤 + 一根 <= now
        lo = max(0, k - limit)
        return [[int(t[i]), c["open"][i], c["high"][i], c["low"][i], c["close"][i], 0.0, int(t[i]) + step - 1,
                 c["quote_volume"][i]] for i in range(lo, k)]

    def get_all_symbols(self):
        return sorted(s for s, (t, _) in self.k1h.items() if t[0] <= self.now - H)

    def fetch_klines(self, symbol, interval, limit):
        if interval == "1d":
            return self._rows(self.btc_1d, D, limit) if symbol == "BTCUSDT" else None
        src, step = (self.k1h, H) if interval == "1h" else (self.k4h, 4 * H)
        return self._rows(src[symbol], step, limit) if symbol in src else None

    def bar(self, symbol, open_ms):
        """open_ms 開盤的 1h K 棒（websocket 收完的那一則），沒有就 None。"""
        if symbol not in self.k1h:
            return None
        t, c = self.k1h[symbol]
        i = int(np.searchsorted(t, open_ms))
        if i >= len(t) or t[i] != open_ms or not np.isfinite(c["close"][i]):
            return None
        return {"s": symbol, "k": {"t": open_ms, "o": str(c["open"][i]), "h": str(c["high"][i]), "l": str(c["low"][i]),
                                   "c": str(c["close"][i]), "x": True}}

    def fetch_prices(self):
        out = {}
        for s, (t, c) in self.k1h.items():
            i = int(np.searchsorted(t, self.now - H, side="right")) - 1
            if i >= 0 and t[i] == self.now - H and np.isfinite(c["close"][i]):
                out[s] = float(c["close"][i])
        return out


def replay(data_dir, start, end, out_dir, breadth_on, kill, max_positions, notional, capital=10_000.0, fee=0.0006,
           btc_path=None):
    start_ms, end_ms = _ms(start), _ms(end)
    t_load = time.time()
    fetcher = ReplayFetcher(Path(data_dir), start_ms, end_ms, btc_path=Path(btc_path) if btc_path else None)
    print(f"loaded {len(fetcher.k1h)} symbols in {time.time() - t_load:.0f}s", flush=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    notif = MagicMock()
    pm = PositionManager(notif, {"TOTAL_CAPITAL": capital, "TRANSACTION_FEE_PERCENT": fee},
                         str(Path(tempfile.mkdtemp(prefix="replay_v31_")) / "positions.csv"))
    pm._save_positions = lambda: None                         # 重播只在記憶體記帳（每筆進出場寫 CSV 在 Windows 會撞檔案鎖）
    live = ShotgunV3Live(fetcher, exclude_symbols=EXCLUDE_SYMBOLS, exclude_bases=NON_CRYPTO_BASES,
                         now_ms=lambda: fetcher.now, fetch_workers=1, rs_mode=RS_MODE,
                         min_daily_quote_vol=MIN_DAILY_QUOTE_VOL, breadth_on=breadth_on,
                         entry_requires_gate_now=kill)        # 原版 v3 preset 照原本慣例：23:00 那根只看前一天
    main._start_shadow = lambda fn: None                      # 影子記錄不在重播範圍
    cfg = {"NOTIONAL_PCT": notional, "DISASTER_STOP_PCT": 0.20, "MAX_POSITIONS": max_positions}

    def gate_step(now, prev):
        if kill:
            return main.hourly_gate(live, pm, notif, now, prev)
        main.refresh_breadth(live, now)
        return live.gate_state(int(now.value // 1_000_000))

    # bootstrap（同 main.bootstrap）：前一天與當天的名單，暖機名單 ∪ 持倉
    fetcher.now = start_ms
    live.daily_rebalance(pd.Timestamp(start_ms - D, unit="ms"))
    top, _ = live.daily_rebalance(pd.Timestamp(start_ms, unit="ms"))
    subscribed = set(top)
    for s in subscribed:
        live.warmup(s)
    gate = gate_step(pd.Timestamp(start_ms, unit="ms"), None)

    equity, t_run, unmonitored = [], time.time(), []
    for t in range(start_ms + H, end_ms + H, H):
        fetcher.now = t
        now = pd.Timestamp(t, unit="ms")
        before = set(subscribed)                              # 這個整點收完的 K 棒是換日前訂閱的
        if t % D == 0:                                        # 換日（同 main.rollover，沒有 websocket）
            top, _ = live.daily_rebalance(now)
            active = set(top) | set(pm.get_open_positions_symbols())
            for s in active - set(live.states):
                live.warmup(s)
            for s in subscribed - active:
                live.states.pop(s, None)
            subscribed = active
        gate = gate_step(now, gate)
        batch = [m for m in (fetcher.bar(s, t - H) for s in before) if m is not None]   # 實盤只收得到訂閱中的幣
        if batch:
            main.handle_batch(batch, live, pm, cfg)
            main.subscribe_open_positions(MagicMock(), pm, subscribed)
        lost = set(pm.get_open_positions_symbols()) - subscribed
        if lost:                                              # 有倉位卻沒訂閱：收不到 K 棒，只剩交易所端災難止損
            unmonitored.append((t, sorted(lost)))
        open_rows = pm.positions_df[pm.positions_df["status"] == "OPEN"]
        floating = float(pd.to_numeric(open_rows["pnl"], errors="coerce").fillna(0).sum()) if len(open_rows) else 0.0
        equity.append((t, pm.paper_equity() + floating, len(open_rows), gate, live.breadth_value))
        if t % (30 * D) == 0:
            print(f"  {now.date()} equity {equity[-1][1]:,.0f} open {len(open_rows)} gate {gate} "
                  f"[{time.time() - t_run:.0f}s]", flush=True)

    eq = pd.DataFrame(equity, columns=["time_ms", "equity", "open", "gate", "breadth"])
    eq["time"] = pd.to_datetime(eq["time_ms"], unit="ms")
    eq.to_csv(out_dir / "equity.csv", index=False)
    tr = pm.positions_df.copy()
    tr.to_csv(out_dir / "trades.csv", index=False)
    curve = eq["equity"].to_numpy()
    dd = curve / np.maximum.accumulate(curve) - 1
    reasons = tr["exit_reason"].value_counts().to_dict()
    print(f"{start}..{end}: {len(tr)} trades, total {100 * (curve[-1] / capital - 1):+.1f}%, max DD {100 * dd.min():.1f}%, "
          f"exits {reasons}, gate on {100 * (eq['gate'] == 'on').mean():.0f}% of hours [{time.time() - t_run:.0f}s]")
    if unmonitored:
        first = unmonitored[0]
        print(f"  WARNING: {len(unmonitored)} hours with open positions not subscribed "
              f"(first {pd.Timestamp(first[0], unit='ms')}: {first[1]})")
    return tr, eq


def main_cli():
    ap = argparse.ArgumentParser(description="Replay history through the live bot code (Shotgun v3.1)")
    ap.add_argument("--data", required=True, help="folder with 1h/<SYMBOL>.pkl and btc_1d.json")
    ap.add_argument("--start", required=True, help="UTC date, a day boundary")
    ap.add_argument("--end", required=True, help="UTC date")
    ap.add_argument("--preset", choices=list(PRESETS), default="v3.1")
    ap.add_argument("--btc", help="BTC daily json if <data>/btc_1d.json has < 1000 days before --start")
    ap.add_argument("--out", default="data/replay_v31")
    ap.add_argument("--fee", type=float, default=0.0006,
                    help="fee per side (default 0.0006 = the bot's TRANSACTION_FEE_PERCENT; the research uses 0.001 incl. slippage)")
    a = ap.parse_args()
    name = f"{a.preset}_{a.start}_{a.end}" + ("" if a.fee == 0.0006 else f"_fee{a.fee:g}")
    replay(a.data, a.start, a.end, Path(a.out) / name, btc_path=a.btc, fee=a.fee, **PRESETS[a.preset])


if __name__ == "__main__":
    main_cli()
