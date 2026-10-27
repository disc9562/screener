# ponytail: 5 種選標的放在同一個檔，agent 才能直接組合；baseline 的固定 20% 止損只當參考點。
"""autoresearch 的 candidate 策略檔（agent 唯一會改的檔，引擎只依賴下面的介面）。

介面：DESCRIPTION、SELECTION、ENTRY_TF、SELECT_WINDOW_4H、TOP_N、
select_score(w, cut_ms, btc_w)、btc_gate(btc_close)、signals(b, aux)、update_stop(sig, i, pos)。
只能用傳進來的資料（全部唯讀）；可以 import 的模組與禁用寫法見 autoresearch/program.md。
鎖死不能改的：出場（開單時框 rma(c,10) < rma(c,20) 以收盤出場）、止損只升不降、手續費。
"""
import numpy as np

from autoresearch.bars import DAY_MS, HOUR_MS, Bars
from autoresearch.indicators import rma, rma_last
from config import DISASTER_STOP_PCT
from strategy import shotgun_v3 as V
from strategy.shadow_lists import martin30

DESCRIPTION = "baseline: rs_v1 + 現行 1h 進場 + 固定 20% 止損"
SELECTION = "rs_v1"  # rs_v1 | rs_v2 | alligator50 | alligator233 | martin30
ENTRY_TF = "1h"  # 5m 15m 30m 1h 2h 4h 8h
SELECT_WINDOW_4H = 300  # 選標的看 cutoff 前最近幾根 4h
TOP_N = 20  # 每天入選幾檔


def _alligator(c: np.ndarray, require_233: bool) -> float | None:
    """V.alligator_4h_score 的邏輯，SMMA 改用 rma_last（較快）。"""
    if len(c) < (250 if require_233 else 100):
        return None
    s10, s20, s50 = (rma_last(c, n) for n in (10, 20, 50))
    last = float(c[-1])
    if not (np.isfinite(s50) and s50 > 0 and last > s10 > s20 > s50):
        return None
    if require_233:
        s233 = rma_last(c, 233)
        if not (np.isfinite(s233) and s50 > s233):
            return None
    score = ((last - s10) + (s10 - s20) + (s20 - s50)) / s50
    return float(score) if np.isfinite(score) and score > 0 else None


def select_score(w: Bars, cut_ms: int, btc_w: Bars) -> float | None:
    """每天 cutoff 的選標的分數。

    w：cutoff 前已收完的最近 SELECT_WINDOW_4H 根 4h（Bars）。
    btc_w：BTCUSDT 用同一規則切出的 4h（Bars）。
    回傳分數（越大越優先，取前 TOP_N 檔），None = 不入選。
    """
    if len(w) < V.RS_BARS + 60 + V.RS_BARS_PER_DAY:
        return None
    if SELECTION == "rs_v1":
        return V.rs_score(w.c, len(w) - 1)
    if SELECTION == "martin30":
        daily_c = w.c[w.t % DAY_MS == 20 * HOUR_MS]  # 20:00 開的 4h 收在 24:00
        r30, ok = martin30(daily_c)
        return r30 if ok and np.isfinite(r30) else None
    if w.t[-1] != cut_ms - 4 * HOUR_MS:  # 資料不新鮮（缺最後一根）
        return None
    if SELECTION == "rs_v2":
        return V.rs_score_v2(w.c, len(w) - 1)
    if SELECTION in ("alligator50", "alligator233"):
        return _alligator(w.c, SELECTION == "alligator233")
    raise ValueError(f"unknown SELECTION: {SELECTION}")


def btc_gate(btc_close: np.ndarray) -> bool:
    """大盤閘門。btc_close：cutoff 前已收完的 BTC 日線收盤價。False = 當天不開新倉。"""
    return len(btc_close) >= 250 and V.btc_bull(btc_close)


def signals(b: Bars, aux) -> dict[str, np.ndarray]:
    """進場訊號（向量化）。b：該幣在 ENTRY_TF 的完整歷史。

    aux：唯讀 mapping，"base5m" = 該幣 5m、"btc5m" = BTCUSDT 5m（Bars），和 b 截在同一個結束時間；
    可用 autoresearch.bars.resample 轉時框。對齊自己處理：第 i 根只能用收盤時間 <= b.t[i] + ENTRY_TF 的 5m。
    必須回傳 "entry"（bool，長度 n）與 "stop"（float，長度 n，第 i 根收盤開倉時的初始止損價）；
    可再加其他陣列給 update_stop 用；每個值都必須是長度 n 的 numpy 陣列。
    鐵則：第 i 個元素只能用 b 的 0..i 的資料，不能偷看未來（引擎會做前綴檢查，違反即淘汰）。
    """
    c = b.c
    s10, s20, s50, s233 = (rma(c, n) for n in V.SMMA_LENS)
    er = V.efficiency_ratio(c)
    with np.errstate(invalid="ignore"):
        entry = (np.isfinite(s233) & (s10 > s20) & (s20 > s50) & (s50 > s233)
                 & (c > s10) & (er >= V.ER_MIN))
    return {"entry": entry, "stop": c * (1 - DISASTER_STOP_PCT)}


def update_stop(sig: dict[str, np.ndarray], i: int, pos) -> float:
    """第 i 根收盤後建議的止損價（引擎只接受上移：stop = max(stop, 回傳值)）。

    sig：signals() 的回傳。pos 有 entry_i、entry_px、init_stop、stop。
    """
    return pos.stop
