"""影子記錄（不下單）：各時框 EMA20 斜率、Martin30 條件、45 度階梯。定義同 core_backtests/research/shotgun_slope_buckets.py。"""
import numpy as np
import pandas as pd

from strategy import shadow_lists as SL

H = 3_600_000
T0 = int(pd.Timestamp("2026-01-01").value // 1_000_000)


def _rows(closes, start_ms, step_ms, spread=1.0):
    return [[start_ms + i * step_ms, c, c + spread, c - spread, c, 1.0, start_ms + (i + 1) * step_ms - 1]
            for i, c in enumerate(closes)]


def test_slope_of_steady_trend_is_rise_per_bar_over_atr():
    # 每根漲 0.5、高低差 2（TR = 2）→ EMA20 每根上升 → 0.5、ATR → 2 → 斜率 ≈ 0.25
    c = 100 + 0.5 * np.arange(300)
    s = SL.ema_slope(c + 1, c - 1, c)
    assert abs(s - 0.25) < 0.01


def test_resample_keeps_only_complete_buckets():
    rows = _rows(np.arange(10, dtype=float), T0, H)          # 10 根 1h → 4h 桶：[0-3]、[4-7] 完整，[8-9] 不完整
    ids, o, h, l, c = SL.resample(rows, 1, 4, cut_ms=T0 + 10 * H)
    assert list(c) == [3.0, 7.0] and list(o) == [0.0, 4.0] and list(h) == [4.0, 8.0]


def test_martin30():
    up = 100 * 1.015 ** np.arange(40)                          # 30 天約 +56%、站上上升的 SMA20
    r30, ok = SL.martin30(up)
    assert ok and abs(r30 - (1.015 ** 30 - 1)) < 1e-9
    assert not SL.martin30(np.full(40, 100.0))[1]


def test_ladder_smallest_timeframe():
    ok = {h: "ok" for h in SL.TFS}
    assert SL.ladder({1: 0.05, 2: 0.2, 4: 0.3, 8: 0.1, 12: 0.0, 24: 0.4}, ok, theta=0.15) == (2, True)
    assert SL.ladder({1: 0.01, 2: 0.02, 4: 0.03, 8: 0.04, 12: 0.05, 24: 0.06}, ok, theta=0.15) == (None, True)


def test_features_use_only_completed_bars():
    k1 = _rows(100 + 0.5 * np.arange(200), T0 - 200 * H, H)
    k4 = _rows(100 + 2.0 * np.arange(300), T0 - 300 * 4 * H, 4 * H)
    f = SL.features(k1, k4, cut_ms=T0)
    assert set(f["slope"]) == {1, 2, 4, 8, 12, 24} and all(np.isfinite(v) for v in f["slope"].values())
    assert f["martin_ok"] and f["r30"] > 0.3
    # 加一根還沒收完的 1h（開盤 = cutoff）不能改變結果
    k1b = k1 + _rows([500.0], T0, H)
    assert SL.features(k1b, k4, cut_ms=T0)["slope"][1] == f["slope"][1]


def test_duplicates_dropped_and_gap_detected():
    rows = _rows(100 + 0.5 * np.arange(200), T0 - 200 * H, H)
    dup = rows[:150] + [rows[149]] + rows[150:]                  # 重複一根 → 去重後照常
    assert SL.features(dup, [], cut_ms=T0)["status"][1] == "ok"
    gap = rows[:180] + rows[181:]                                # 最近 40 根內缺一根 → gap，不能當等間隔算
    f = SL.features(gap, [], cut_ms=T0)
    assert f["status"][1] == "gap" and np.isnan(f["slope"][1])


def test_stale_when_latest_completed_bar_missing():
    rows = _rows(100 + 0.5 * np.arange(200), T0 - 203 * H, H)    # 最後一根在 cutoff 前 3 小時就結束
    assert SL.features(rows, [], cut_ms=T0)["status"][1] == "stale"


def test_fetch_failed_is_not_below_threshold_and_blocks_ladder():
    k4 = _rows(100 * 1.01 ** np.arange(300), T0 - 300 * 4 * H, 4 * H, spread=0.5)
    f = SL.features(None, k4, cut_ms=T0)
    assert f["status"][1] == "fetch_failed" and f["status"][2] == "fetch_failed"
    assert f["status"][4] == "ok" and f["slope"][4] >= SL.THETA     # 4h 達標，但 1h/2h 沒資料
    assert f["ladder"] is None and f["ladder_complete"] is False    # 不能宣稱「最小在 4h 達標」


def test_martin_needs_consecutive_days():
    k4 = _rows(100 * 1.003 ** np.arange(300), T0 - 300 * 4 * H, 4 * H)
    assert SL.features([], k4, cut_ms=T0)["martin_status"] == "ok"
    holed = k4[:250] + k4[256:]                                     # 少一整天的 4h
    f = SL.features([], holed, cut_ms=T0)
    assert f["martin_status"] == "gap" and f["martin_ok"] is False
