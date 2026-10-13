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
        if pos is not None and V.exit_signal(s10[i], s20[i]):
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
