"""輕量級離線歷史回測工具：直接對比 Shotgun v3 的三種選標的邏輯（RS v1 vs RS v2 vs Martin30）。

特色：
1. 兩階段精簡抓取（預設只取前 80 大活躍永續合約，且只對曾入選 Top 20 的幣抓 1h K 線），總流量 < 8 MB。
2. 所有抓過的 K 線自動快取至 data/backtest_cache/，第二次執行 0 網路流量、秒級完成。
3. 100% 複用 strategy/shotgun_v3.py 與 strategy/shadow_lists.py 的實盤函數。
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import EQUITY_LIKE_BASES, EXCLUDE_SYMBOLS, DISASTER_STOP_PCT, NOTIONAL_PCT, TRANSACTION_FEE_PERCENT
from strategy import shadow_lists as SL
from strategy import shotgun_v3 as V

FAPI_BASE = "https://fapi.binance.com"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "backtest_cache"


def _http_get_json(path: str, params: dict | None = None, delay: float = 0.2) -> list | dict:
    url = f"{FAPI_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "screener-backtest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if delay > 0:
        time.sleep(delay)
    return data


def _cached_json(name: str, fetcher_fn, refresh: bool = False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        data = fetcher_fn()
    except Exception:
        return []
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _is_excluded(sym: str) -> bool:
    if sym in EXCLUDE_SYMBOLS:
        return True
    base = sym[:-4] if sym.endswith("USDT") else sym
    if base in EQUITY_LIKE_BASES:
        return True
    if base.startswith("1000") and base[4:] in EQUITY_LIKE_BASES:
        return True
    return False


def get_liquid_universe(top_n: int = 80, refresh: bool = False) -> list[str]:
    info = _cached_json("exchange_info", lambda: _http_get_json("/fapi/v1/exchangeInfo", delay=0.1), refresh=refresh)
    valid = {
        s["symbol"]
        for s in info.get("symbols", [])
        if s.get("quoteAsset") == "USDT"
        and s.get("contractType") == "PERPETUAL"
        and s.get("status") == "TRADING"
        and not _is_excluded(s["symbol"])
    }
    tickers = _cached_json("tickers_24h", lambda: _http_get_json("/fapi/v1/ticker/24hr", delay=0.1), refresh=refresh)
    ranked = sorted(
        (t for t in tickers if t.get("symbol") in valid),
        key=lambda t: float(t.get("quoteVolume", 0.0)),
        reverse=True,
    )
    return [t["symbol"] for t in ranked[:top_n]]


def fetch_klines_cached(symbol: str, interval: str, limit: int, refresh: bool = False, delay: float = 0.2) -> list:
    key = f"klines_{symbol}_{interval}_{limit}"
    return _cached_json(
        key,
        lambda: _http_get_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}, delay=delay),
        refresh=refresh,
    )


def build_daily_lists(rows_4h_map: dict[str, list], days: list[pd.Timestamp], min_qv: float = 0.0):
    """對每個回測日 cutoff (00:00 UTC) 計算選股名單：rs_v1, rs_v2, alligator50, alligator233, martin30。"""
    lists = {"rs_v1": {}, "rs_v2": {}, "alligator50": {}, "alligator233": {}, "martin30": {}}
    # 預先轉成 numpy 陣列加速
    parsed = {}
    for sym, rows in rows_4h_map.items():
        if not rows:
            continue
        t = np.array([int(r[0]) for r in rows], dtype=np.int64)
        c = np.array([float(r[4]) for r in rows], dtype=float)
        qv = np.array([float(r[7]) if len(r) > 7 else float(r[4]) * float(r[5]) for r in rows], dtype=float)
        parsed[sym] = (t, c, qv, rows)

    for day in days:
        cut_ms = int(day.value // 1_000_000)
        s_v1, s_v2, s_alg50, s_alg233, s_m30 = {}, {}, {}, {}, {}
        for sym, (t, c, qv, raw_rows) in parsed.items():
            idx = int(np.searchsorted(t, cut_ms, side="left"))
            if idx < V.RS_BARS + 60 + V.RS_BARS_PER_DAY:
                continue
            # 1. 原版 RS v1
            sc1 = V.rs_score(c[:idx], idx - 1)
            if sc1 is not None:
                s_v1[sym] = sc1

            # 2. 優化版 RS v2 與 4h 鱷魚選股（含新鮮度檢查）
            if t[idx - 1] == cut_ms - 4 * V.HOUR_MS:
                avg_qv = float(qv[max(0, idx - 42):idx].sum() / 7.0)
                if avg_qv >= min_qv:
                    sc2 = V.rs_score_v2(c[:idx], idx - 1)
                    if sc2 is not None:
                        s_v2[sym] = sc2
                    alg50 = V.alligator_4h_score(c[:idx], idx - 1, require_233=False)
                    if alg50 is not None:
                        s_alg50[sym] = alg50
                    alg233 = V.alligator_4h_score(c[:idx], idx - 1, require_233=True)
                    if alg233 is not None:
                        s_alg233[sym] = alg233

            # 3. Martin30（用 4h 重取樣日線）
            _, _, _, _, daily_c = SL.resample(raw_rows[:idx], 4, 24, cut_ms)
            r30, ok = SL.martin30(daily_c)
            if ok and np.isfinite(r30):
                s_m30[sym] = r30

        lists["rs_v1"][day.date()] = frozenset(V.rank_top(s_v1))
        lists["rs_v2"][day.date()] = frozenset(V.rank_top(s_v2))
        lists["alligator50"][day.date()] = frozenset(V.rank_top(s_alg50))
        lists["alligator233"][day.date()] = frozenset(V.rank_top(s_alg233))
        lists["martin30"][day.date()] = frozenset(V.rank_top(s_m30))
    return lists


def simulate_arm(rows_1h_map: dict[str, list], daily_gate: dict, bull_dates: set | None, start_ms: int):
    """用與實盤 PositionManager + ShotgunV3Live 相同的規則模擬交易。"""
    # 收集所有時間點的事件以按時間推進權益與倉位
    sym_data = {}
    all_times = set()
    for sym, rows in rows_1h_map.items():
        if len(rows) < V.SMMA_LENS[-1] + V.ER_LEN + 10:
            continue
        t = np.array([int(r[0]) for r in rows], dtype=np.int64)
        hi = np.array([float(r[2]) for r in rows], dtype=float)
        lo = np.array([float(r[3]) for r in rows], dtype=float)
        c = np.array([float(r[4]) for r in rows], dtype=float)
        s10, s20, s50, s233, er = V.indicators(c)
        idx_map = {int(t[i]): i for i in range(len(t)) if int(t[i]) >= start_ms}
        sym_data[sym] = (t, hi, lo, c, s10, s20, s50, s233, er, idx_map)
        all_times.update(idx_map.keys())

    equity = 10_000.0
    peak_equity = equity
    max_dd = 0.0
    open_pos = {}  # sym -> dict(entry_price, units, stop_loss, entry_ms)
    trades = []

    for ts_ms in sorted(all_times):
        day = pd.Timestamp(ts_ms, unit="ms").date()
        gate = daily_gate.get(day, frozenset())
        is_bull = True if bull_dates is None else (day in bull_dates)

        # 1. 先檢查持倉出場（災難止損優先，其次 SMMA10 < SMMA20 死叉）
        for sym in sorted(list(open_pos.keys())):
            sd = sym_data.get(sym)
            if not sd or ts_ms not in sd[9]:
                continue
            _, hi, lo, c, s10, s20, s50, s233, er, idx_map = sd
            i = idx_map[ts_ms]
            pos = open_pos[sym]
            exit_px, reason = None, None
            if lo[i] <= pos["stop_loss"]:
                exit_px, reason = pos["stop_loss"], "DISASTER_STOP"
            elif V.exit_signal(s10[i], s20[i]):
                exit_px, reason = float(c[i]), "CROSS_10_20"

            if exit_px is not None:
                open_fee = pos["entry_price"] * pos["units"] * TRANSACTION_FEE_PERCENT
                close_fee = exit_px * pos["units"] * TRANSACTION_FEE_PERCENT
                pnl = (exit_px - pos["entry_price"]) * pos["units"] - open_fee - close_fee
                ret_pct = (exit_px / pos["entry_price"] - 1.0) - 2 * TRANSACTION_FEE_PERCENT
                equity += pnl
                peak_equity = max(peak_equity, equity)
                dd = (equity - peak_equity) / peak_equity
                max_dd = min(max_dd, dd)
                trades.append({
                    "symbol": sym,
                    "entry_ms": pos["entry_ms"],
                    "exit_ms": ts_ms,
                    "entry_px": pos["entry_price"],
                    "exit_px": exit_px,
                    "pnl": pnl,
                    "ret_pct": ret_pct,
                    "reason": reason,
                    "hold_hours": (ts_ms - pos["entry_ms"]) / V.HOUR_MS,
                })
                del open_pos[sym]

        # 2. 再檢查無持倉且在當日名單的進場
        if is_bull:
            for sym in sorted(gate):
                if sym in open_pos:
                    continue
                sd = sym_data.get(sym)
                if not sd or ts_ms not in sd[9]:
                    continue
                _, hi, lo, c, s10, s20, s50, s233, er, idx_map = sd
                i = idx_map[ts_ms]
                if V.entry_signal(c[i], s10[i], s20[i], s50[i], s233[i], er[i]):
                    px = float(c[i])
                    units = (equity * NOTIONAL_PCT) / px
                    open_pos[sym] = {
                        "entry_price": px,
                        "units": units,
                        "stop_loss": px * (1.0 - DISASTER_STOP_PCT),
                        "entry_ms": ts_ms,
                    }

    return trades, equity, max_dd


def summarize(name: str, trades: list[dict], final_equity: float, max_dd: float) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "策略組別": name, "交易筆數": 0, "勝率(%)": 0.0, "獲利因子(PF)": 0.0,
            "平均單筆(%)": 0.0, "總報酬(%)": 0.0, "最大回撤(%)": 0.0, "災難止損數": 0, "平均持倉(h)": 0.0,
        }
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [-t["pnl"] for t in trades if t["pnl"] <= 0]
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else float("inf")
    avg_ret = float(np.mean([t["ret_pct"] for t in trades]) * 100)
    stops = sum(1 for t in trades if t["reason"] == "DISASTER_STOP")
    avg_hold = float(np.mean([t["hold_hours"] for t in trades]))
    return {
        "策略組別": name,
        "交易筆數": n,
        "勝率(%)": round(len(wins) / n * 100, 1),
        "獲利因子(PF)": round(pf, 2),
        "平均單筆(%)": round(avg_ret, 2),
        "總報酬(%)": round((final_equity / 10_000.0 - 1.0) * 100, 2),
        "最大回撤(%)": round(max_dd * 100, 2),
        "災難止損數": stops,
        "平均持倉(h)": round(avg_hold, 1),
    }


def main():
    ap = argparse.ArgumentParser(description="Lightweight Shotgun v3 Target Selection Backtest")
    ap.add_argument("--universe-size", type=int, default=60, help="Top N liquid symbols to scan (default: 60)")
    ap.add_argument("--min-qv", type=float, default=0.0, help="Min 7d avg daily quote volume for RS v2")
    ap.add_argument("--refresh", action="store_true", help="Force re-download from Binance")
    args = ap.parse_args()

    print(f"[1/4] 取得前 {args.universe_size} 大主流活躍合約清單...")
    universe = get_liquid_universe(top_n=args.universe_size, refresh=args.refresh)
    print(f"      共 {len(universe)} 檔標的（已排除穩定幣與美股代幣）")

    print("[2/4] 下載／讀取快取 4h K 線（750 根 ≈ 125 天）與 BTC 日線...")
    btc_1d = fetch_klines_cached("BTCUSDT", "1d", 1000, refresh=args.refresh, delay=0.15)
    rows_4h_map = {}
    for idx, sym in enumerate(universe, 1):
        rows_4h_map[sym] = fetch_klines_cached(sym, "4h", 750, refresh=args.refresh, delay=0.15)
        if idx % 15 == 0 or idx == len(universe):
            print(f"      4h K 線進度：{idx}/{len(universe)}")

    # 決定 1h 回測區間：取最近 1500 根 1h（約 62 天），前 14 天當 SMMA233 暖機，後 48 天為正式交易區間
    latest_4h_ms = max(int(rows[-1][0]) for rows in rows_4h_map.values() if rows)
    end_day = pd.Timestamp(latest_4h_ms, unit="ms").normalize()
    start_day = end_day - pd.Timedelta(days=48)
    days = [start_day + pd.Timedelta(days=i) for i in range(49)]

    # 計算 BTC bull dates
    btc_t = np.array([int(r[0]) for r in btc_1d], dtype=np.int64)
    btc_c = np.array([float(r[4]) for r in btc_1d], dtype=float)
    bull_dates = set()
    for d in days:
        cut_ms = int(d.value // 1_000_000)
        b_idx = int(np.searchsorted(btc_t, cut_ms, side="left"))
        if b_idx >= 250 and V.btc_bull(btc_c[:b_idx]):
            bull_dates.add(d.date())

    print(f"[3/4] 計算每日 Top 20 名單（區間：{start_day.date()} ~ {end_day.date()}，共 {len(days)} 天，BTC 多頭日：{len(bull_dates)} 天）...")
    lists = build_daily_lists(rows_4h_map, days, min_qv=args.min_qv)

    # 找出曾入選任一組 Top 20 的所有標的，只對這些幣抓 1h K 線
    ever_selected = set()
    for arm_map in lists.values():
        for s_set in arm_map.values():
            ever_selected |= set(s_set)
    ever_selected = sorted(ever_selected)
    print(f"[4/4] 只對曾入選過 Top 20 的 {len(ever_selected)} 檔標的下載／讀取 1h K 線（1500 根 ≈ 62 天）...")
    rows_1h_map = {}
    for idx, sym in enumerate(ever_selected, 1):
        rows_1h_map[sym] = fetch_klines_cached(sym, "1h", 1500, refresh=args.refresh, delay=0.15)
        if idx % 15 == 0 or idx == len(ever_selected):
            print(f"      1h K 線進度：{idx}/{len(ever_selected)}")

    start_ms = int(start_day.value // 1_000_000)
    arms_spec = [
        ("rs_v1", "1. 原版 SMA 30/45/60 (RS v1)"),
        ("rs_v2", "2. 優化 SMA 30/45/60 (RS v2)"),
        ("alligator50", "3. 4h 鱷魚 SMMA 10>20>50"),
        ("alligator233", "4. 4h 鱷魚 SMMA 10>20>50>233"),
        ("martin30", "5. Martin30 (日線趨勢+30d>=30%)"),
    ]
    print("\n================ 回測結果（全天候選股能力對比：不限 BTC 開關）================")
    rows_all = []
    for key, label in arms_spec:
        tr, eq, mdd = simulate_arm(rows_1h_map, lists[key], bull_dates=None, start_ms=start_ms)
        rows_all.append(summarize(label, tr, eq, mdd))
    print(pd.DataFrame(rows_all).to_string(index=False))

    print(f"\n================ 回測結果（開啟 BTC 日線 EMA200 多頭開關：共 {len(bull_dates)}/{len(days)} 多頭日）================")
    rows_bull = []
    for key, label in arms_spec:
        tr, eq, mdd = simulate_arm(rows_1h_map, lists[key], bull_dates=bull_dates, start_ms=start_ms)
        rows_bull.append(summarize(label, tr, eq, mdd))
    print(pd.DataFrame(rows_bull).to_string(index=False))


if __name__ == "__main__":
    main()
