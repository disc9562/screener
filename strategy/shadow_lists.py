"""影子記錄用的純函式（不下單、沒有 I/O）：各時框 EMA20 斜率、Martin30 條件、45 度階梯。

定義與 core_backtests 研究相同（research/shotgun_slope_buckets.py、research/shotgun_screen_martin.py）：
  斜率 = (EMA20[t] − EMA20[t−5]) / (5 × ATR14[t])，每根 K 棒上升幾個 ATR，與圖表縮放無關；只用 cutoff 前已收完的 K 棒。
  Martin30 = 30 日漲幅 ≥ 30%、日收盤 > SMA20、SMA20 高於前一天。
  階梯 = 最小在哪個時框斜率 ≥ θ（θ = 0.15 是看過歷史才定的示意值；CSV 存原始斜率與狀態，之後可用任何 θ 重算）。
資料品質（Codex 2026-10-19 審查）：每個時框都標狀態，缺值不能當成「沒達標」：
  ok｜fetch_failed（沒抓到資料）｜insufficient（根數不夠）｜stale（最後一根不是 cutoff 前剛收完的那根）｜gap（最近 NEED 根內不連續）
"""
import numpy as np
import pandas as pd

TFS = (1, 2, 4, 8, 12, 24)
THETA = 0.15
K = 5
NEED = 40              # 斜率：最近 40 根必須連續、齊全
NEED_MARTIN = 31       # Martin30：最近 31 個日線必須連續
HOUR_MS = 3_600_000


def resample(rows, base_h, h, cut_ms):
    """kline rows（[open_ms, o, h, l, c, v, close_ms]，每根 base_h 小時）→ h 小時 K 棒的 (桶編號, open, high, low, close)。
    先去掉重複開盤時間、排序；UTC 對齊；只保留 cutoff 前完整收完、而且根數齊全的桶。桶編號 = 開盤時間 // h 小時，用來檢查連續性。"""
    a = np.array([[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in (rows or [])
                  if int(r[0]) + base_h * HOUR_MS <= cut_ms], dtype=float).reshape(-1, 5)
    empty = (np.array([], dtype=np.int64),) + (np.array([]),) * 4
    if not len(a):
        return empty
    df = pd.DataFrame(a, columns=["t", "o", "h", "l", "c"]).drop_duplicates("t", keep="last").sort_values("t")
    df["b"] = (df["t"] // (h * HOUR_MS)).astype(np.int64)
    g = df.groupby("b").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("t", "size"))
    g = g[(g["n"] == h // base_h) & ((g.index + 1) * h * HOUR_MS <= cut_ms)]
    return g.index.to_numpy(np.int64), g["o"].to_numpy(), g["h"].to_numpy(), g["l"].to_numpy(), g["c"].to_numpy()


def series_status(ids, h, cut_ms, need, had_rows=True):
    if not had_rows:
        return "fetch_failed"
    if not len(ids):
        return "insufficient"
    if ids[-1] != cut_ms // (h * HOUR_MS) - 1:
        return "stale"
    if len(ids) < need:
        return "insufficient"
    return "ok" if np.all(np.diff(ids[-need:]) == 1) else "gap"


def ema_slope(high, low, close, k=K):
    """最後一根的 EMA20 斜率（ATR/根）；資料不足回 NaN。"""
    close = np.asarray(close, float)
    if len(close) < 30:
        return np.nan
    ema = pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy()
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    return float((ema[-1] - ema[-1 - k]) / (k * atr[-1])) if atr[-1] > 0 else np.nan


def martin30(daily_close):
    """回傳 (30 日漲幅, 是否符合 Martin30)。呼叫端要先確認日線連續。"""
    c = np.asarray(daily_close, float)
    if len(c) < 31:
        return np.nan, False
    r30 = c[-1] / c[-31] - 1
    sma, sma_prev = c[-20:].mean(), c[-21:-1].mean()
    return float(r30), bool(r30 >= 0.30 and c[-1] > sma and sma > sma_prev)


def ladder(slopes, status, theta=THETA):
    """回傳 (最小達標時框或 None, 是否可信)。比它小的時框只要有一個狀態不是 ok，就不能宣稱「最小」→ (None, False)。"""
    for h in TFS:
        if status.get(h) != "ok":
            return None, False
        if slopes[h] >= theta:
            return h, True
    return None, True


def features(rows_1h, rows_4h, cut_ms):
    """一個幣在 cut_ms 當下的影子特徵：1/2h 斜率用 1h 資料，4/8/12/24h 斜率與 Martin30 用 4h 資料；每項都附狀態。"""
    slope, status = {}, {}
    for h in TFS:
        base, rows = (1, rows_1h) if h < 4 else (4, rows_4h)
        ids, _, hi, lo, c = resample(rows, base, h, cut_ms)
        status[h] = series_status(ids, h, cut_ms, NEED, had_rows=bool(rows))
        slope[h] = ema_slope(hi, lo, c) if status[h] == "ok" else np.nan
    ids, *_, daily = resample(rows_4h, 4, 24, cut_ms)
    m_status = series_status(ids, 24, cut_ms, NEED_MARTIN, had_rows=bool(rows_4h))
    r30, ok = martin30(daily) if m_status == "ok" else (np.nan, False)
    lad, complete = ladder(slope, status)
    return dict(slope=slope, status=status, r30=r30, martin_ok=ok, martin_status=m_status, ladder=lad, ladder_complete=complete)
