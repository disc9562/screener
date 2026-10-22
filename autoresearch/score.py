"""評分層：校準 r 使總回撤落在 24%–26%、淘汰規則、前綴（偷看未來）檢查、評分 CLI。"""
import argparse
import importlib
import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import numpy as np

from autoresearch import data
from autoresearch.bars import DAY_MS, MIN_MS, Bars, resample
from autoresearch.engine import SimResult, Trade, build_daily_gates, prepare, simulate

BAND = (0.24, 0.26)
R0 = 0.04
R_MAX = 0.05
MAX_RUNS = 5
MIN_TRADES = 100
COIN_DD_MAX = 0.40
FIXED_STOP_SHARE = 0.95
EQUITY0 = 10_000.0


@dataclass
class Calib:
    r: float
    result: SimResult
    runs: int
    status: str  # in_band | capped | unreachable | bankrupt


def calibrate(run: Callable[[float], SimResult]) -> Calib:
    r, lo, hi = R0, None, None  # lo: 回撤太小時的 r；hi: 回撤太大時的 r
    for k in range(1, MAX_RUNS + 1):
        used, res = r, run(r)
        if res.bankrupt:
            return Calib(r, res, k, "bankrupt")
        m = res.max_dd
        if BAND[0] <= m <= BAND[1]:
            return Calib(r, res, k, "in_band")
        if m < BAND[0] and r >= R_MAX:
            return Calib(r, res, k, "capped")
        if m < BAND[0]:
            lo = r
        else:
            hi = r
        if k < 3 or lo is None or hi is None:
            r = r * 0.25 / m if m > 0 else R_MAX
        else:
            r = (lo + hi) / 2
        r = min(r, R_MAX)
    return Calib(used, res, MAX_RUNS, "unreachable")


def coin_drawdowns(trades: list[Trade]) -> dict[str, float]:
    by: dict[str, list[Trade]] = {}
    for t in trades:
        by.setdefault(t.symbol, []).append(t)
    out = {}
    for s, ts in by.items():
        ts = sorted(ts, key=lambda t: t.exit_ms)
        curve = np.r_[1.0, np.cumprod(1 + np.array([t.ret_pct for t in ts]))]
        out[s] = float(np.max(1 - curve / np.maximum.accumulate(curve)))
    return out


def fixed_pct_stop(trades: list[Trade]) -> bool:
    if not trades:
        return False
    d = Counter(round((t.entry_px - t.init_stop) / t.entry_px, 3) for t in trades)
    return max(d.values()) / len(trades) >= FIXED_STOP_SHARE


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b))


def _view(trades, cut):
    """cut 之前就已決定的部分：entry_ms <= cut 的進場；cut 前已正常出場者再比出場。"""
    out = []
    for t in trades:
        if t.entry_ms > cut:
            continue
        done = t.exit_ms < cut and t.reason != "END"
        out.append((t.symbol, t.entry_ms, t.exit_ms if done else None, t.reason if done else None,
                    t.entry_px, t.exit_px if done else None, t.units))
    return sorted(out, key=lambda x: (x[1], x[0]))


def prefix_ok(cand, base5m, gates, r, start_ms, end_ms, full: SimResult, cuts: list[int]) -> bool:
    for cut in cuts:
        trunc = {s: b.window(0, int(np.searchsorted(b.t + 5 * MIN_MS, cut, "right")))
                 for s, b in base5m.items()}
        g = {d: v for d, v in gates.items() if d < cut}
        res = simulate(cand, prepare(cand, trunc, cut), g, r, start_ms, cut)
        a, b = _view(full.trades, cut), _view(res.trades, cut)
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if x[:4] != y[:4]:
                return False
            for p, q in zip(x[4:], y[4:]):
                if (p is None) != (q is None) or (p is not None and not _close(p, q)):
                    return False
    return True


def yearly(curve_t, curve_eq, equity0: float) -> list[dict]:
    if len(curve_t) == 0:
        return []
    ms = np.asarray(curve_t, "int64") - 1  # 整點收在年初的那根算前一年
    years = ms.astype("datetime64[ms]").astype("datetime64[Y]").astype(int) + 1970
    eqs = np.asarray(curve_eq, float)
    out, start = [], float(equity0)
    for y in np.unique(years):
        eq = eqs[years == y]
        path = np.r_[start, eq]
        out.append({"year": int(y), "return": float(eq[-1] / start - 1),
                    "mdd": float(np.max(1 - path / np.maximum.accumulate(path)))})
        start = float(eq[-1])
    return out


def coin_profit_share(trades, k: int = 3) -> list[tuple[str, float]]:
    pnl: dict[str, float] = {}
    for t in trades:
        pnl[t.symbol] = pnl.get(t.symbol, 0.0) + t.pnl
    pos = {s: v for s, v in pnl.items() if v > 0}
    tot = sum(pos.values())
    return [(s, v / tot) for s, v in sorted(pos.items(), key=lambda kv: -kv[1])[:k]]


def evaluate(cand, base5m: dict[str, Bars], universe: list[str], *, baseline: bool, seed: int) -> dict:
    start_ms, end_ms = data.START_MS, data.END_MS  # 呼叫時才讀，測試可 monkeypatch
    secs = {"gates": 0.0, "prepare": 0.0, "calibrate": 0.0, "prefix": 0.0}
    out = {"status": "fail", "reason": "", "description": getattr(cand, "DESCRIPTION", ""),
           "entry_tf": getattr(cand, "ENTRY_TF", None), "select_window": getattr(cand, "SELECT_WINDOW_4H", None),
           "top_n": getattr(cand, "TOP_N", None), "r": None, "mdd": None, "total_return": None,
           "trades": None, "max_coin_dd": None, "win_rate": None, "profit_factor": None,
           "yearly": [], "top_coins": [], "seconds": secs}
    try:
        t0 = time.time()
        bars_4h = {s: resample(base5m[s], 240) for s in universe}
        btc_1d = resample(base5m[data.BTC], 1440)
        gates = build_daily_gates(cand, bars_4h, btc_1d, start_ms, end_ms)
        secs["gates"] = time.time() - t0
        t0 = time.time()
        uni5m = {s: base5m[s] for s in universe}
        p = prepare(cand, uni5m, end_ms)
        secs["prepare"] = time.time() - t0

        t0 = time.time()
        cal = calibrate(lambda r: simulate(cand, p, gates, r, start_ms, end_ms, equity0=EQUITY0))
        secs["calibrate"] = time.time() - t0
        res, tr = cal.result, cal.result.trades
        wins = sum(t.pnl for t in tr if t.pnl > 0)
        losses = sum(-t.pnl for t in tr if t.pnl < 0)
        cdd = coin_drawdowns(tr)
        out.update(r=cal.r, mdd=res.max_dd, total_return=res.final_equity / EQUITY0 - 1, trades=len(tr),
                   max_coin_dd=max(cdd.values(), default=0.0),
                   win_rate=sum(t.pnl > 0 for t in tr) / len(tr) if tr else 0.0,
                   profit_factor=wins / losses if losses > 0 else float("inf"),
                   yearly=yearly(res.curve_t, res.curve_eq, EQUITY0),
                   top_coins=[[s, v] for s, v in coin_profit_share(tr)])

        if cal.status == "bankrupt":
            out["reason"] = "bankrupt"
        elif cal.status == "unreachable":
            out["reason"] = "mdd_unreachable"
        elif len(tr) <= MIN_TRADES:
            out["reason"] = "trades<=100"
        elif out["max_coin_dd"] > COIN_DD_MAX:
            out["reason"] = "coin_dd>40%"
        elif not baseline and fixed_pct_stop(tr):
            out["reason"] = "fixed_pct_stop"
        else:
            t0 = time.time()
            rng = random.Random(seed)
            span = end_ms - start_ms
            cuts = [int(rng.uniform(start_ms + 0.2 * span, end_ms - 0.05 * span)) // DAY_MS * DAY_MS
                    for _ in range(2)]
            ok = prefix_ok(cand, uni5m, gates, cal.r, start_ms, end_ms, res, cuts)
            secs["prefix"] = time.time() - t0
            if ok:
                out.update(status="pass", reason=cal.status)
            else:
                out["reason"] = "lookahead"
    except Exception as e:
        out["status"], out["reason"] = "fail", f"error: {type(e).__name__}: {str(e)[:200]}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--baseline", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.modules.pop("strategy.candidate", None)
        cand = importlib.import_module("strategy.candidate")
        universe, base5m = data.load_all()
        out = evaluate(cand, base5m, universe, baseline=a.baseline, seed=a.seed)
    except Exception as e:
        out = {"status": "fail", "reason": f"error: {type(e).__name__}: {str(e)[:200]}"}
    with open(a.out, "w") as f:
        json.dump(out, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
