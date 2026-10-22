"""回測引擎：candidate + 5m 快取 → 交易、每小時 MTM 權益曲線、最大回撤。"""
from dataclasses import dataclass, replace

import numpy as np

from autoresearch.bars import DAY_MS, Bars, HOUR_MS, TF_MINUTES, resample
from autoresearch.indicators import rma
from config import TRANSACTION_FEE_PERCENT


class CandidateError(Exception):
    pass


@dataclass
class Position:
    symbol: str
    entry_i: int
    entry_ms: int
    entry_px: float
    units: float
    init_stop: float
    stop: float


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_ms: int
    exit_ms: int
    entry_px: float
    exit_px: float
    init_stop: float
    units: float
    pnl: float
    ret_pct: float
    reason: str  # STOP | CROSS_10_20 | END


@dataclass
class SimResult:
    trades: list
    curve_t: np.ndarray
    curve_eq: np.ndarray
    final_equity: float
    max_dd: float
    bankrupt: bool


@dataclass
class Prepared:
    tf_ms: int
    bars: dict
    sig: dict
    exit_mask: dict
    close_1h: dict


def build_daily_gates(cand, bars_4h, btc_1d, start_ms, end_ms):
    gates = {}
    syms = sorted(bars_4h)
    cuts = list(range(start_ms // DAY_MS * DAY_MS, end_ms, DAY_MS))
    ks = {s: np.searchsorted(bars_4h[s].t, cuts, "left") for s in syms}
    for j, cut in enumerate(cuts):
        if not cand.btc_gate(btc_1d.c[btc_1d.t < cut]):
            gates[cut] = frozenset()
            continue
        scored = []
        for s in syms:
            k = int(ks[s][j])
            w = bars_4h[s].window(max(0, k - cand.SELECT_WINDOW_4H), k)
            if len(w) == 0:
                continue
            sc = cand.select_score(w, cut)
            if sc is None or not np.isfinite(sc):
                continue
            scored.append((-float(sc), s))
        scored.sort()
        gates[cut] = frozenset(s for _, s in scored[:cand.TOP_N])
    return gates


def _readonly(b):
    arrs = [a.copy() for a in (b.t, b.o, b.h, b.l, b.c, b.v, b.qv)]
    for a in arrs:
        a.setflags(write=False)
    return Bars(*arrs)


def _prefix(sig, i):
    out = {}
    for k, v in sig.items():
        v = np.asarray(v)[:i + 1]
        v.setflags(write=False)
        out[k] = v
    return out


def _pos_int(x):
    return isinstance(x, (int, np.integer)) and not isinstance(x, bool) and x > 0


def prepare(cand, base5m, end_ms):
    tf = getattr(cand, "ENTRY_TF", None)
    if tf not in TF_MINUTES or tf == "1d":
        raise CandidateError(f"bad ENTRY_TF: {tf!r}")
    if not _pos_int(getattr(cand, "SELECT_WINDOW_4H", None)) or not _pos_int(getattr(cand, "TOP_N", None)):
        raise CandidateError("SELECT_WINDOW_4H and TOP_N must be positive ints")
    for name in ("select_score", "btc_gate", "signals", "update_stop"):
        if not callable(getattr(cand, name, None)):
            raise CandidateError(f"missing function: {name}")
    tf_ms = TF_MINUTES[tf] * 60_000
    p = Prepared(tf_ms, {}, {}, {}, {})
    for s in sorted(base5m):
        raw = base5m[s]
        b = raw if tf == "5m" else resample(raw, TF_MINUTES[tf])
        b = b.window(0, int(np.searchsorted(b.t + tf_ms, end_ms, "right")))
        if len(b) == 0:
            continue
        b = _readonly(b)  # 共用的 5m 陣列不能被 candidate 改到
        mask = rma(b.c, 10) < rma(b.c, 20)  # 在 signals 之前算，鎖死的出場不受影響
        sig = cand.signals(b)
        for key in ("entry", "stop"):
            if not isinstance(sig, dict) or key not in sig:
                raise CandidateError(f"signals() missing {key!r}")
            if len(sig[key]) != len(b):
                raise CandidateError(f"signals()[{key!r}] length {len(sig[key])} != {len(b)}")
        p.bars[s] = b
        p.sig[s] = sig
        p.exit_mask[s] = mask
        p.close_1h[s] = resample(raw, 60)
    return p


def mtm_curve(trades, close_1h, start_ms, end_ms, equity0):
    H = np.arange(start_ms + HOUR_MS, end_ms + 1, HOUR_MS, dtype=np.int64)
    eq = np.full(len(H), float(equity0))
    realized = np.zeros(len(H) + 1)
    for tr in trades:
        a = int(np.searchsorted(H, tr.entry_ms, "left"))
        e = int(np.searchsorted(H, tr.exit_ms, "left"))
        realized[e] += tr.pnl
        if e > a:
            b1 = close_1h[tr.symbol]
            idx = np.searchsorted(b1.t, H[a:e] - HOUR_MS, "right") - 1
            px = np.where(idx >= 0, b1.c[np.maximum(idx, 0)], tr.entry_px)
            eq[a:e] += tr.units * (px - tr.entry_px)
    eq += np.cumsum(realized)[:len(H)]
    return H, eq


def simulate(cand, p, gates, r, start_ms, end_ms, exposure_cap=4.0, equity0=10_000.0):
    fee = TRANSACTION_FEE_PERCENT  # 呼叫時才讀，測試可 monkeypatch
    tf = p.tf_ms
    bars = {}
    for s, b in p.bars.items():
        n = int(np.searchsorted(b.t + tf, end_ms, "right"))
        if n > 0:
            bars[s] = b.window(0, n)
    syms = sorted(bars)
    entry_at = {}  # 開盤時間 -> 有進場訊號的幣（依 symbol 排序）
    for s in syms:
        b = bars[s]
        ent = np.asarray(p.sig[s]["entry"], bool)[:len(b)]
        for i in np.flatnonzero(ent & (b.t >= start_ms)):
            entry_at.setdefault(int(b.t[i]), []).append(s)
    if syms:
        times = np.unique(np.concatenate([bars[s].t[bars[s].t >= start_ms] for s in syms]))
    else:
        times = np.empty(0, np.int64)

    eq = float(equity0)
    open_ = {}
    exposure = 0.0
    trades = []
    bankrupt = False

    def close(pos, px, ms, reason):
        nonlocal eq, exposure
        pnl = (px - pos.entry_px) * pos.units - fee * (pos.entry_px + px) * pos.units
        trades.append(Trade(pos.symbol, pos.entry_ms, ms, pos.entry_px, px, pos.init_stop,
                            pos.units, pnl, px / pos.entry_px - 1 - 2 * fee, reason))
        eq += pnl
        exposure -= pos.units * pos.entry_px
        del open_[pos.symbol]

    for T in times:
        T = int(T)
        if not open_ and T not in entry_at:
            continue
        for s in sorted(open_):
            b = bars[s]
            i = int(np.searchsorted(b.t, T))
            if i >= len(b) or b.t[i] != T:
                continue
            pos = open_[s]
            if b.l[i] <= pos.stop:
                close(pos, pos.stop, T + tf, "STOP")
            elif p.exit_mask[s][i]:
                close(pos, float(b.c[i]), T + tf, "CROSS_10_20")
            else:
                new = cand.update_stop(_prefix(p.sig[s], i), i, replace(pos))
                if new is not None and np.isfinite(new):
                    pos.stop = max(pos.stop, min(float(new), float(b.c[i])))
        if eq <= 0:
            bankrupt = True
            break
        gate = gates.get(T // DAY_MS * DAY_MS, frozenset())
        for s in entry_at.get(T, ()):
            if s in open_ or s not in gate:
                continue
            b = bars[s]
            i = int(np.searchsorted(b.t, T))
            px = float(b.c[i])
            stop = float(p.sig[s]["stop"][i])
            if not (np.isfinite(px) and np.isfinite(stop) and 0 < stop < px):
                continue
            units = eq * r / (px - stop)
            notional = units * px
            if exposure + notional > exposure_cap * eq:
                continue
            open_[s] = Position(s, i, T + tf, px, units, stop, stop)
            exposure += notional

    for s in sorted(open_):
        close(open_[s], float(bars[s].c[-1]), end_ms, "END")

    curve_t, curve_eq = mtm_curve(trades, p.close_1h, start_ms, end_ms, equity0)
    if len(curve_eq):
        final = float(curve_eq[-1])
        full = np.r_[float(equity0), curve_eq]
        max_dd = float(np.max(1 - full / np.maximum.accumulate(full)))
        bankrupt = bankrupt or bool(curve_eq.min() <= 0)
    else:
        final, max_dd = eq, 0.0
    return SimResult(trades, curve_t, curve_eq, final, max_dd, bankrupt)
