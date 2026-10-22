# Autoresearch Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `screener` 裡建一個 autoresearch 迴圈：agent 每輪改 `strategy/candidate.py` 的一個想法，腳本用 2021-01-01～2026-10-22 的 5m 資料回測、在固定回撤下評分、決定留或丟，最後列出前三名。

**Architecture:** 新增 `autoresearch/` 套件。`data.py` 下載並快取 5m K 線；`bars.py`／`indicators.py` 提供重新取樣與快速 SMMA；`engine.py` 是回測引擎（選標的、進出場、止損、曝險、逐小時權益）；`score.py` 做部位換算、淘汰檢查、輸出 JSON；`loop.py` 控制回合、呼叫 `claude -p`、用 git commit／tag 保存版本、寫 `results.tsv` 和報告。agent 只碰 `strategy/candidate.py`。

**Tech Stack:** Python 3.14、numpy、pandas（都已安裝）、pytest、git、`claude` CLI。不新增套件。

**Spec:** `docs/superpowers/specs/2026-10-22-autoresearch-design.md`

## Global Constraints

- 回測期間：`START_MS` = 2021-01-01 00:00 UTC；`END_MS` = 2026-10-22 00:00 UTC（不含）。只計 `START_MS` 以後開倉的交易。
- 暖機抓取起點：各幣 2020-11-01；BTCUSDT 2020-01-01。
- 選股池：`tools/backtest_compare.get_liquid_universe(top_n=60)`，第一次執行存成固定清單，之後不再更新。
- 基礎資料是 5m。其他時框都從 5m 重新取樣：15m、30m、1h、2h、4h、8h、1d。
- `ENTRY_TF` 只允許：`5m`、`15m`、`30m`、`1h`、`2h`、`4h`、`8h`。
- 每筆風險 r：起點 `0.04`，上限 `0.05`。部位 = 已實現權益 × r / (開倉價 − 初始止損)。
- 總回撤目標區間 `[0.24, 0.26]`；每輪最多 5 次回測。r = 0.05 時回撤仍 < 0.24 → 以 r = 0.05 參賽。
- 總曝險上限 `4.0` × 已實現權益（以開倉名目計）。超過就跳過該筆開倉。
- 淘汰：交易筆數 ≤ 100；任一幣種單獨回撤（定義 B）> 0.40；權益 ≤ 0；回撤調不進區間；前綴檢查失敗；固定百分比止損（非 baseline，≥ 95% 交易的止損距離四捨五入到 0.1% 後相同）。
- 止損只升不降：`stop = max(stop, update_stop(...))`，從下一根開單時框 K 線生效。低點碰到止損就以止損價成交。
- 出場邏輯鎖定：開單時框上 `rma(c,10) < rma(c,20)` 就以收盤價出場。
- 手續費：`config.TRANSACTION_FEE_PERCENT`，開倉、平倉各收一次。不模擬資金費率、不模擬跳空。
- 選標的視窗：每天 cutoff（00:00 UTC）前已收完的最近 `SELECT_WINDOW_4H` 根 4h（baseline = 300，和 `strategy/shotgun_v3_live.py:69` 一致）。
- 不修改實盤程式：`strategy/shotgun_v3.py`、`strategy/shadow_lists.py`、`strategy/shotgun_v3_live.py`、`main.py`、`config.py`。
- 快取目錄：環境變數 `AR_CACHE_DIR`；預設是主 repo 的 `data/backtest_cache/autoresearch/`（主 repo 路徑 = `git rev-parse --path-format=absolute --git-common-dir` 的上一層）。這樣所有 worktree 共用同一份資料，而且已被 `.gitignore` 的 `data/backtest_cache/` 排除。
- `--rounds` 預設 30。迴圈只在 `autoresearch/` 開頭的分支上執行。
- 這台開發主機不下載真實 K 線。所有測試用合成資料或 mock。只有 `python -m autoresearch.data` 會連網下載，其他指令缺資料時只報錯、不下載。
- 測試流程依 `dataflow-ut`：先實作，再一次設計最小測試集，跑一次，只修失敗的。測試檔放 `tests/test_ar_*.py`，沿用 `tests/test_shotgun_v3.py` 的寫法（`from strategy import shotgun_v3 as V`、隨機漫步產生價格）。

## Review Focus

1. **5 個 baseline 全被淘汰**（固定 20% 止損很可能讓某個幣連續止損 3 次，單獨回撤 > 40%）→ 迴圈仍要啟動：取總報酬最高的 baseline 當起點，`best_score = -inf`，第一個通過的版本成為第 1 名。測試：Task 6 `test_round0_starts_even_if_all_baselines_fail`。
2. **candidate 程式碼丟例外、卡住、回傳形狀不對**（缺 `stop`、長度不符、NaN、止損 ≥ 收盤價）→ 這一輪 `discard` 並寫明原因，迴圈繼續。測試：Task 4 `test_prepare_rejects_bad_candidate`、`test_entry_requires_gate_start_and_valid_stop`，Task 5 `test_evaluate_reports_error_and_few_trades`。
3. **2021 年後才上市或資料有缺口的幣**（月檔 404、陣列較短）→ 不當機，上市前不被選中。測試：Task 2 `test_download_symbol_skips_before_onboard_and_falls_back_on_404`，Task 4 `test_build_daily_gates_window_and_btc_gate`（短陣列）。
4. **agent 改了別的檔案、刪掉 candidate、或沒改任何東西** → `discard`，工作目錄還原到第 1 名。測試：Task 6 `test_round_flow_keep_pass_discard`。
5. **使用者在回合中按 Ctrl-C** → 分支 reset 到第 1 名、報告照樣寫出。測試：Task 6 `test_keyboard_interrupt_resets_and_reports`。

---

### Task 1: Bars、重新取樣、快速 SMMA

**Files:**
- Create: `autoresearch/__init__.py`（空檔）
- Create: `autoresearch/bars.py`
- Create: `autoresearch/indicators.py`
- Test: `tests/test_ar_bars.py`

**Interfaces:**
- Produces:
  - `bars.Bars`：`@dataclass(frozen=True)`，欄位 `t`（int64，開盤時間 ms）、`o`、`h`、`l`、`c`、`v`、`qv`（float64）。方法 `__len__() -> int`、`window(a: int, b: int) -> Bars`（各欄 `[a:b]` 的 view）。
  - `bars.TF_MINUTES: dict[str, int]` = `{"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"8h":480,"1d":1440}`
  - `bars.MIN_MS = 60_000`、`bars.HOUR_MS = 3_600_000`、`bars.DAY_MS = 86_400_000`
  - `bars.resample(b: Bars, minutes: int) -> Bars`
  - `indicators.rma(x: np.ndarray, n: int) -> np.ndarray`：數值等於 `V.rma_seeded`
  - `indicators.rma_last(x: np.ndarray, n: int) -> float`：等於 `V.rma_seeded(x, n)[-1]`；`len(x) < n` 回傳 `nan`

- [ ] **Step 1: 實作 `bars.py`**

`resample`：分組鍵 `t // (minutes*MIN_MS) * (minutes*MIN_MS)`；o = 組內第一筆、h = max、l = min、c = 最後一筆、v 和 qv = 加總；輸出 `t` = 分組鍵。缺幾根 5m 的組照樣輸出。用 `np.unique(..., return_index=True)` 加 `np.maximum.reduceat`／`np.minimum.reduceat`／`np.add.reduceat`，不用 pandas groupby。

- [ ] **Step 2: 實作 `indicators.py`**

```python
def rma(x, n):
    # ponytail: pandas ewm(adjust=False, alpha=1/n) 就是 Pine rma 的遞迴，只差種子；把種子放進第一格。
    x = np.asarray(x, float); out = np.full(len(x), np.nan)
    if len(x) < n: return out
    seeded = np.r_[x[:n].mean(), x[n:]]
    out[n - 1:] = pd.Series(seeded).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    return out

def rma_last(x, n):
    # 只要最後一個值：a^m·seed + Σ (1−a)·a^(m−j)·x_j，O(len) 一次內積。選標的每天要呼叫十幾萬次，不能走 pandas。
    a = (n - 1) / n; m = len(x) - n
    if m < 0: return float("nan")
    w = (1 - a) * a ** np.arange(m - 1, -1, -1)
    return float(a ** m * np.mean(x[:n]) + w @ np.asarray(x[n:], float))
```

- [ ] **Step 3: 寫最小測試集 `tests/test_ar_bars.py`**

```python
def test_resample_aggregates_ohlcv():          # covers: 分組鍵→t、o/h/l/c/v/qv 各聚合
    b = 24 根 5m（t = 0, 300000, ...），o=arange, h=o+2, l=o-1, c=o+1, v=1, qv=2
    r = resample(b, 60)
    assert r.t.tolist() == [0, 3_600_000]
    assert r.o.tolist() == [0, 12] and r.c.tolist() == [12, 24]
    assert r.h.tolist() == [13, 25] and r.l.tolist() == [-1, 11]
    assert r.v.tolist() == [12, 12] and r.qv.tolist() == [24, 24]

def test_resample_keeps_partial_group():       # covers: 缺根的組仍輸出、t 對齊
    刪掉第 3~5 根 → resample(b, 60) 仍是 2 根，第一根 v == 9

def test_rma_matches_rma_seeded():             # covers: 種子格、遞迴段
    c = 隨機漫步 1200 根
    for n in (10, 20, 50, 233):
        assert np.allclose(rma(c, n), V.rma_seeded(c, n), rtol=1e-12, equal_nan=True)

def test_rma_last_matches_and_short_input():   # covers: m>=0 分支、m<0 分支（邊界）
    assert math.isclose(rma_last(c[:300], 233), V.rma_seeded(c[:300], 233)[-1], rel_tol=1e-10)
    assert math.isclose(rma_last(c[:233], 233), c[:233].mean(), rel_tol=1e-12)
    assert math.isnan(rma_last(c[:100], 233))
```

- [ ] **Step 4: 跑測試**

Run: `pytest tests/test_ar_bars.py -q`
Expected: `4 passed`。失敗只修失敗的那一個。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/__init__.py autoresearch/bars.py autoresearch/indicators.py tests/test_ar_bars.py
git commit -m "feat(autoresearch): add Bars, resample and fast rma helpers"
```

---

### Task 2: 5m 資料下載、快取、固定選股池

**Files:**
- Create: `autoresearch/data.py`
- Test: `tests/test_ar_data.py`

**Interfaces:**
- Consumes: `bars.Bars`；`tools.backtest_compare._http_get_json(path, params, delay)`、`tools.backtest_compare.get_liquid_universe(top_n, refresh)`、`tools.backtest_compare._cached_json`（取 `exchange_info` 的 `onboardDate`）
- Produces:
  - 常數：`START_MS`、`END_MS`、`FETCH_START_MS`、`BTC_FETCH_START_MS`、`BTC = "BTCUSDT"`、`CACHE_DIR: Path`
  - `month_starts(start_ms: int, end_ms: int) -> list[int]`：`[start, end)` 內每個月 1 日 00:00 UTC
  - `parse_kline_csv(text: str) -> np.ndarray`：shape `(n, 7)`，欄位 = CSV 的第 0,1,2,3,4,5,7 欄（t,o,h,l,c,v,qv）；第一行不是數字就略過（新檔有表頭，舊檔沒有）
  - `fetch_month_zip(sym: str, month_ms: int) -> np.ndarray | None`：URL `https://data.binance.vision/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{YYYY}-{MM}.zip`；HTTP 404 回 `None`，其他錯誤往外丟
  - `fetch_api(sym: str, start_ms: int, end_ms: int) -> np.ndarray`：`/fapi/v1/klines`，`interval=5m`、`limit=1500`、`startTime` 分頁
  - `download_symbol(sym: str, start_ms: int, end_ms: int, onboard_ms: int) -> Bars`：存 `CACHE_DIR/5m/{sym}.npz`
  - `save_5m(sym: str, b: Bars) -> None`：`np.savez_compressed` 到 `CACHE_DIR/5m/{sym}.npz`（`download_symbol` 和 Task 7 的合成資料都用它）
  - `load_5m(sym: str) -> Bars | None`
  - `freeze_universe(n: int = 60) -> list[str]`：讀寫 `CACHE_DIR/universe.json`（`{"created": iso, "symbols": [...]}`）
  - `ensure_data() -> list[str]`：補齊選股池和 BTC 缺的檔，回傳選股池。所有檔案都已完整時不連網（`exchange_info` 只在有檔案要下載時才抓）
  - `load_all() -> tuple[list[str], dict[str, Bars]]`：只讀快取；有缺檔就丟 `FileNotFoundError`，訊息列出缺哪些幣
  - CLI：`python -m autoresearch.data`

- [ ] **Step 1: 實作 `data.py`**

`download_symbol` 的流程：
```python
for m in month_starts(start_ms, end_ms):
    nxt = 下個月 1 日
    if nxt <= onboard_ms: continue                       # 還沒上市的月份不抓
    if nxt <= end_ms: rows = fetch_month_zip(sym, m); rows = rows if rows is not None else fetch_api(sym, m, nxt)
    else:             rows = fetch_api(sym, m, end_ms)   # 當月：月檔還沒出
合併 → 依 t 去重、排序 → 只留 start_ms <= t < end_ms → np.savez_compressed
```
`fetch_api`：每次 `startTime = 上一批最後一根 t + 5*MIN_MS`，回傳不足 1500 根或已到 `end_ms` 就停；過濾 `t < end_ms`。`ensure_data`：檔案已存在且最後一根 `t >= END_MS - 5*MIN_MS` 就跳過；每個幣印一行進度。`onboardDate` 取不到時當 0。

- [ ] **Step 2: 寫最小測試集 `tests/test_ar_data.py`**（全部 mock 網路，`monkeypatch.setattr(data, "CACHE_DIR", tmp_path)`）

```python
def test_parse_kline_csv_with_and_without_header():     # covers: 表頭判斷兩個分支、欄位挑選
    兩行資料（12 欄）各測一次有表頭、沒表頭
    assert arr.shape == (2, 7) and arr[0, 6] == 第 7 欄的值

def test_fetch_api_paginates_until_end(monkeypatch):     # covers: startTime 遞增、停止條件、end 過濾
    假 _http_get_json：第一次回 1500 根、第二次回 10 根
    assert 第二次呼叫的 startTime == 第一批最後 t + 300_000
    assert 只呼叫 2 次 and 結果全部 t < end_ms

def test_download_symbol_skips_before_onboard_and_falls_back_on_404(monkeypatch):
    # covers: onboard 跳過、404→API、當月→API、存檔→load_5m
    onboard = 2021-03-15；假 fetch_month_zip 對 2021-04 回 None
    assert 沒有請求 2021-01、2021-02 的月檔
    assert fetch_api 被呼叫在 2021-04 整月、以及最後一個未結束的月份
    assert load_5m(sym) 的 t 和回傳值相同、嚴格遞增、無重複

def test_freeze_universe_writes_once(monkeypatch):       # covers: 檔案不存在→寫、存在→讀
    假 get_liquid_universe 計次
    freeze_universe(); freeze_universe()
    assert 計次 == 1 and 兩次回傳相同
```

- [ ] **Step 3: 跑測試**

Run: `pytest tests/test_ar_data.py -q`
Expected: `4 passed`。

真實下載和抽查不在這次開發範圍，移到文件最後的「在實驗主機上執行」。

- [ ] **Step 4: Commit**

```bash
git add autoresearch/data.py tests/test_ar_data.py
git commit -m "feat(autoresearch): download and cache 5m klines, freeze universe"
```

---

### Task 3: candidate 介面與 baseline 範本

**Files:**
- Create: `autoresearch/baseline_candidate.py`（範本，`SELECTION = "rs_v1"`）
- Create: `autoresearch/baselines.py`
- Create: `strategy/candidate.py`（內容 = 範本原樣）
- Test: `tests/test_ar_baseline.py`

**Interfaces:**
- Consumes: `bars.Bars`、`bars.HOUR_MS`、`bars.DAY_MS`、`indicators.rma`、`indicators.rma_last`、`strategy.shotgun_v3`（`rs_score`、`rs_score_v2`、`efficiency_ratio`、`btc_bull`、`RS_BARS`、`RS_BARS_PER_DAY`、`ER_MIN`）、`strategy.shadow_lists.martin30`、`config.DISASTER_STOP_PCT`
- Produces（candidate 介面，engine 和 agent 都依這個）：
  - `DESCRIPTION: str`、`SELECTION: str`、`ENTRY_TF: str = "1h"`、`SELECT_WINDOW_4H: int = 300`、`TOP_N: int = 20`
  - `select_score(w: Bars, cut_ms: int) -> float | None`：`w` = cutoff 前已收完的最近 `SELECT_WINDOW_4H` 根 4h；`None` = 不入選
  - `btc_gate(btc_close: np.ndarray) -> bool`：`btc_close` = cutoff 前已收完的 BTC 日線收盤價
  - `signals(b: Bars) -> dict[str, np.ndarray]`：`b` = 該幣在 `ENTRY_TF` 的完整歷史；必有 `entry`（bool，長度 n）和 `stop`（float，長度 n，在第 i 根收盤開倉時的初始止損價）；可加其他陣列給 `update_stop` 用
  - `update_stop(sig: dict[str, np.ndarray], i: int, pos) -> float`：第 i 根收盤後的建議止損；`pos` 有 `entry_i`、`entry_px`、`init_stop`、`stop`
  - `baselines.BASELINES = ("rs_v1", "rs_v2", "alligator50", "alligator233", "martin30")`
  - `baselines.variant_source(selection: str) -> str`：讀範本，替換 `SELECTION = "..."` 和 `DESCRIPTION = "..."` 兩行（`DESCRIPTION = "baseline: {selection} + 現行 1h 進場 + 固定 20% 止損"`）

- [ ] **Step 1: 實作 `autoresearch/baseline_candidate.py`**

各 `SELECTION` 的規則（對應 `tools/backtest_compare.py:110`–`134`）：
- 全部：`len(w) < V.RS_BARS + 60 + V.RS_BARS_PER_DAY` → `None`
- `rs_v1`：`V.rs_score(w.c, len(w) - 1)`
- `rs_v2`：要求新鮮 `w.t[-1] == cut_ms - 4 * HOUR_MS`，否則 `None`；`V.rs_score_v2(w.c, len(w) - 1)`
- `alligator50`／`alligator233`：要求新鮮；`_alligator(w.c, require_233)`，邏輯照抄 `V.alligator_4h_score`（`strategy/shotgun_v3.py:110`），但 SMMA 改用 `rma_last`
- `martin30`：日線收盤 = `w.c[w.t % DAY_MS == 20 * HOUR_MS]`；`r30, ok = martin30(daily_c)`；`ok and isfinite(r30)` 才回 `r30`

`btc_gate`：`len(btc_close) >= 250 and V.btc_bull(btc_close)`。
`signals`：`s10,s20,s50,s233 = rma(c, n)`、`er = V.efficiency_ratio(c)`；`entry = isfinite(s233) & (s10>s20) & (s20>s50) & (s50>s233) & (c>s10) & (er>=V.ER_MIN)`（向量版 `V.entry_signal`）；`stop = c * (1 - DISASTER_STOP_PCT)`。
`update_stop`：`return pos.stop`。
檔頭註解寫：`# ponytail: 5 種選標的放在同一個檔，agent 才能直接組合；baseline 的固定 20% 止損只當參考點。`

- [ ] **Step 2: 實作 `baselines.py`，並建立 `strategy/candidate.py`**

Run: `cp autoresearch/baseline_candidate.py strategy/candidate.py`

- [ ] **Step 3: 寫最小測試集 `tests/test_ar_baseline.py`**

```python
def test_selection_scores_match_live_functions():   # covers: 5 個 SELECTION 分支→回傳值
    4h 隨機漫步 300 根，t 對齊到最後一根 = cut - 4h
    rs_v1  == V.rs_score(c, 299)；rs_v2 == V.rs_score_v2(c, 299)
    alligator50/233 ≈ V.alligator_4h_score(c, 299, require_233=False/True)（rel 1e-9；兩邊都 None 也算相等）
    martin30 == SL.martin30(同一組日線)[0]（ok 為 True 的種子）

def test_select_score_none_when_stale_or_short():   # covers: 長度門檻（邊界 233/234）、新鮮度分支
    233 根 → 全部 None；234 根 rs_v1 不是 None
    最後一根 t != cut - 4h → rs_v2、alligator 為 None，rs_v1 不受影響

def test_signals_entry_matches_entry_signal():      # covers: entry 各條件、stop
    1h 隨機漫步 1500 根（drift 為正，確保有 True）
    ref = [V.entry_signal(c[i], *(x[i] for x in V.indicators(c))) for i in range(n)]
    assert sig["entry"].tolist() == ref and any(ref)
    assert np.allclose(sig["stop"], c * 0.8)

def test_btc_gate_needs_250_days():                  # covers: 長度門檻（邊界）
    249 根遞增 → False；250 根遞增 → True

def test_variant_source_sets_selection():           # covers: 兩行替換
    src = variant_source("martin30")
    assert 'SELECTION = "martin30"' in src and "martin30" in 描述那一行
    compile(src, "candidate.py", "exec")
```
把 `SELECTION` 切換寫成 `importlib` 載入範本後 `monkeypatch.setattr(mod, "SELECTION", ...)`。

- [ ] **Step 4: 跑測試**

Run: `pytest tests/test_ar_baseline.py -q`
Expected: `5 passed`。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/baseline_candidate.py autoresearch/baselines.py strategy/candidate.py tests/test_ar_baseline.py
git commit -m "feat(autoresearch): candidate interface and five baseline selections"
```

---

### Task 4: 回測引擎

**Files:**
- Create: `autoresearch/engine.py`
- Test: `tests/test_ar_engine.py`

**Interfaces:**
- Consumes: `bars.*`、`indicators.rma`、candidate 介面（Task 3）、`config.TRANSACTION_FEE_PERCENT`
- Produces:
  - `class CandidateError(Exception)`
  - `@dataclass Position`：`symbol: str`、`entry_i: int`、`entry_ms: int`、`entry_px: float`、`units: float`、`init_stop: float`、`stop: float`
  - `@dataclass(frozen=True) Trade`：`symbol`、`entry_ms`、`exit_ms`、`entry_px`、`exit_px`、`init_stop`、`units`、`pnl`、`ret_pct`、`reason`（`"STOP"`｜`"CROSS_10_20"`｜`"END"`）
  - `@dataclass SimResult`：`trades: list[Trade]`、`curve_t: np.ndarray`、`curve_eq: np.ndarray`、`final_equity: float`、`max_dd: float`（正數比例）、`bankrupt: bool`
  - `@dataclass Prepared`：`tf_ms: int`、`bars: dict[str, Bars]`、`sig: dict[str, dict]`、`exit_mask: dict[str, np.ndarray]`、`close_1h: dict[str, Bars]`
  - `build_daily_gates(cand, bars_4h: dict[str, Bars], btc_1d: Bars, start_ms: int, end_ms: int) -> dict[int, frozenset[str]]`：key = 每天 00:00 UTC 的 ms
  - `prepare(cand, base5m: dict[str, Bars], end_ms: int) -> Prepared`
  - `simulate(cand, p: Prepared, gates: dict[int, frozenset[str]], r: float, start_ms: int, end_ms: int, exposure_cap: float = 4.0, equity0: float = 10_000.0) -> SimResult`
  - `mtm_curve(trades: list[Trade], close_1h: dict[str, Bars], start_ms: int, end_ms: int, equity0: float) -> tuple[np.ndarray, np.ndarray]`

- [ ] **Step 1: 實作 `build_daily_gates` 和 `prepare`**

`build_daily_gates`：每天 `cut`：每幣 `k = searchsorted(t, cut, "left")`，`w = window(max(0, k - SELECT_WINDOW_4H), k)`；`len(w) == 0` 跳過；`select_score` 回傳 `None` 或非有限值就不入選；名單 = 依 `(-score, symbol)` 排序取前 `TOP_N`；`btc_gate(btc_1d.c[btc_1d.t < cut])` 為 False 時當天名單是空集合。

`prepare`：檢查 `ENTRY_TF in TF_MINUTES` 且不是 `"1d"`、`SELECT_WINDOW_4H`／`TOP_N` 是正整數、四個函式都存在，否則丟 `CandidateError`。每幣：`b = resample(base5m[s], TF_MINUTES[ENTRY_TF])`（5m 直接用原資料），只留 `t + tf_ms <= end_ms`；`sig = cand.signals(b)`，檢查 `entry`、`stop` 存在、長度等於 `len(b)`，否則丟 `CandidateError`；`exit_mask = rma(c,10) < rma(c,20)`；`close_1h = resample(base5m[s], 60)`。

- [ ] **Step 2: 實作 `simulate` 和 `mtm_curve`**

時間語意：開單時框第 i 根在 `t[i]` 開盤、`t[i] + tf_ms` 收盤；所有判斷都在收盤時做；`entry_ms`／`exit_ms` 都記收盤時間。當天名單用 `t[i] // DAY_MS * DAY_MS` 查（和 `strategy/shotgun_v3_live.py:129` 的 `day_info(open_ms)` 一致）。

```python
eq = equity0；open = {}；依時間走過所有出現過的 bar 開盤時間 T
每個 T：先對所有持倉做第 1 步（依 symbol 排序），再對所有候選幣做第 2 步（依 symbol 排序）
  1. 持倉 s 在 T 有 bar（索引 i）：
       l[i] <= pos.stop          → 以 pos.stop 平倉，reason "STOP"
       elif exit_mask[s][i]      → 以 c[i] 平倉，reason "CROSS_10_20"
       else: new = update_stop(sig[s], i, pos)；有限值才 pos.stop = max(pos.stop, new)
             # 下一根才會被第 1 步檢查到 → 「從下一根生效」自然成立
     平倉：pnl = (exit − entry)·units − fee·(entry + exit)·units；ret_pct = exit/entry − 1 − 2·fee；eq += pnl
     eq <= 0 → bankrupt=True，停止模擬
  2. 幣 s 在 T 有 bar（索引 i）、沒持倉、t[i] >= start_ms、s ∈ gates[day]、sig["entry"][i]、stop = sig["stop"][i] 有限且 0 < stop < c[i]：
       units = eq·r / (c[i] − stop)；notional = units·c[i]
       Σ(open 的 units·entry_px) + notional <= exposure_cap·eq 才開倉，否則跳過
到 end：剩下的持倉以各自最後一根收盤價平倉，reason "END"，exit_ms = end_ms
curve_t, curve_eq = mtm_curve(...)；max_dd = max(1 − eq/cummax(eq))；final_equity = curve_eq[-1]
curve 最小值 <= 0 也算 bankrupt
```
加速只做一件事：每幣先算出 `entry` 為 True 的索引，進場檢查只看這些時間點；持倉的止損與出場仍逐根檢查。

`mtm_curve`：標記時間 `H = arange(start_ms + HOUR_MS, end_ms + 1, HOUR_MS)`（每小時收盤）。已實現權益 = `equity0 + Σ(exit_ms <= h 的 pnl)`。每筆交易在 `entry_ms <= h < exit_ms` 的標記加上 `units·(close_1h(h) − entry_px)`；`close_1h(h)` 取開盤時間 `h − HOUR_MS` 的 1h 收盤價，缺根往前補值。

- [ ] **Step 3: 寫最小測試集 `tests/test_ar_engine.py`**

用 `types.SimpleNamespace` 做假 candidate，直接造 1h 的 `Bars` 和 `Prepared`（`exit_mask` 手動指定），`fee` 用 `monkeypatch.setattr(engine, "TRANSACTION_FEE_PERCENT", 0.0)` 讓數字好算。

```python
def test_build_daily_gates_window_and_btc_gate():     # covers: 視窗切片、None 過濾、排序與同分、btc 關閉、短陣列
    假 select_score 記錄 len(w) 與 w.t[-1]
    assert 每次 len(w) <= SELECT_WINDOW_4H and w.t[-1] < cut
    TOP_N=1、兩幣同分 → 名單是字母小的那個；只有 50 根的幣在前期不出錯
    btc_gate 回 False 的那天 → frozenset()

def test_simulate_sizes_by_risk_and_fills_stop():     # covers: units 公式、STOP 成交價、pnl
    第 k 根收盤 100、stop 90、r=0.04、equity 10000 → units == 40
    第 k+2 根 low 89 → Trade(exit_px=90, reason="STOP")，pnl == -400，final_equity == 9600

def test_stop_only_ratchets_up_from_next_bar():       # covers: max(old,new)、生效時間
    update_stop：第 k+1 根回 95、第 k+2 根回 80
    第 k+1 根 low 94（新止損還沒生效）→ 不出場
    第 k+2 根 low 94.5 → 以 95 出場（沒被 80 拉低）

def test_exit_cross_and_end_close():                   # covers: CROSS_10_20 分支、END 分支
    A 幣在第 j 根 exit_mask=True → 以 c[j] 出場；B 幣一直持有 → reason "END"、exit_ms == end_ms

def test_exposure_cap_skips_entry():                   # covers: 曝險判斷兩個出口
    A、B 同一根進場，止損距離 1.6%、r=0.04 → 各自名目 250%
    assert 只有 A 開倉（2.5 + 2.5 > 4.0）

def test_entry_requires_gate_start_and_valid_stop():   # covers: gate、start_ms、stop 有效性（NaN、>= 收盤）
    不在名單、t < start_ms、stop = nan、stop = c[i] → 都沒有交易

def test_mtm_drawdown_counts_open_loss():              # covers: 未實現損益進 curve、max_dd
    持倉中價格從 100 跌到 70 再回到 110 才出場
    assert max_dd ≈ 40·30 / 10000（浮虧有算進去）and 交易本身是獲利

def test_bankrupt_flag():                               # covers: eq <= 0 分支
    r = 2.0、止損 50% → 一次止損權益變負 → bankrupt True、之後沒有新交易

def test_prepare_rejects_bad_candidate():              # covers: CandidateError 各出口
    ENTRY_TF="3h"、signals 缺 "stop"、entry 長度少 1 → 都丟 CandidateError
```

- [ ] **Step 4: 跑測試**

Run: `pytest tests/test_ar_engine.py -q`
Expected: `9 passed`。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/engine.py tests/test_ar_engine.py
git commit -m "feat(autoresearch): backtest engine with risk sizing, ratchet stops, exposure cap, hourly MTM"
```

---

### Task 5: 評分、淘汰、前綴檢查、評分 CLI

**Files:**
- Create: `autoresearch/score.py`
- Test: `tests/test_ar_score.py`

**Interfaces:**
- Consumes: `engine.*`、`data.load_all`、`data.START_MS`、`data.END_MS`、`data.BTC`、`bars.resample`
- Produces:
  - 常數：`BAND = (0.24, 0.26)`、`R0 = 0.04`、`R_MAX = 0.05`、`MAX_RUNS = 5`、`MIN_TRADES = 100`、`COIN_DD_MAX = 0.40`、`FIXED_STOP_SHARE = 0.95`
  - `@dataclass Calib`：`r: float`、`result: SimResult`、`runs: int`、`status: str`（`"in_band"`｜`"capped"`｜`"unreachable"`｜`"bankrupt"`）
  - `calibrate(run: Callable[[float], SimResult]) -> Calib`
  - `coin_drawdowns(trades: list[Trade]) -> dict[str, float]`
  - `fixed_pct_stop(trades: list[Trade]) -> bool`
  - `prefix_ok(cand, base5m, gates, r, start_ms, end_ms, full: SimResult, cuts: list[int]) -> bool`
  - `yearly(curve_t, curve_eq, equity0: float) -> list[dict]`：每年 `{"year", "return", "mdd"}`
  - `coin_profit_share(trades, k: int = 3) -> list[tuple[str, float]]`
  - `evaluate(cand, base5m: dict[str, Bars], universe: list[str], *, baseline: bool, seed: int) -> dict`：JSON 可序列化，鍵：`status`（`"pass"`｜`"fail"`）、`reason`、`description`、`entry_tf`、`select_window`、`top_n`、`r`、`mdd`、`total_return`、`trades`、`max_coin_dd`、`win_rate`、`profit_factor`、`yearly`、`top_coins`、`seconds`
  - CLI：`python -m autoresearch.score --out PATH --seed N [--baseline]`：重新 import `strategy.candidate`，`data.load_all()` 讀資料，結果寫進 `PATH`

- [ ] **Step 1: 實作 `calibrate`**

```python
r, lo, hi = R0, None, None              # lo: 回撤太小時的 r；hi: 回撤太大時的 r
for k in range(1, MAX_RUNS + 1):
    used, res = r, run(r)
    if res.bankrupt: return Calib(r, res, k, "bankrupt")
    m = res.max_dd
    if BAND[0] <= m <= BAND[1]: return Calib(r, res, k, "in_band")
    if m < BAND[0] and r >= R_MAX: return Calib(r, res, k, "capped")
    if m < BAND[0]: lo = r
    else: hi = r
    if k < 3 or lo is None or hi is None:   # 前兩次比例換算；只有一邊界時也用比例
        r = r * 0.25 / m if m > 0 else R_MAX
    else:
        r = (lo + hi) / 2
    r = min(r, R_MAX)
return Calib(used, res, MAX_RUNS, "unreachable")
```

- [ ] **Step 2: 實作其餘函式和 `evaluate`**

- `coin_drawdowns`：每幣依 `exit_ms` 排序，`curve = cumprod(1 + ret_pct)`，從 1.0 起算，`dd = max(1 − curve / cummax)`。
- `fixed_pct_stop`：`d = round((entry_px − init_stop) / entry_px, 3)`；出現最多的值佔比 `>= 0.95` 回 True；沒有交易回 False。
- `prefix_ok`：對每個 `cut`：`base5m` 只留 `t + 5*MIN_MS <= cut`，`gates` 只留 `day < cut`，重新 `prepare` 再 `simulate(..., end_ms=cut)`。比較兩邊 `exit_ms < cut` 且 `reason != "END"` 的交易：`symbol`、`entry_ms`、`exit_ms`、`reason` 完全相同，`entry_px`、`exit_px`、`units` 相對誤差 ≤ 1e-9。任何一個 cut 不同就回 False。
- `evaluate` 的 cut：`random.Random(seed)` 在 `[start + 20% 期間, end − 5% 期間]` 抽 2 個時間，取到整天。
- `evaluate` 的順序與 `reason` 字串（第一個命中的就回傳 `status="fail"`）：
  1. `prepare`／`build_daily_gates` 丟例外 → `"error: <類別>: <訊息前 200 字>"`
  2. `calibrate` 狀態 `bankrupt` → `"bankrupt"`；`unreachable` → `"mdd_unreachable"`
  3. 交易數 `<= MIN_TRADES` → `"trades<=100"`
  4. 最大單幣回撤 `> COIN_DD_MAX` → `"coin_dd>40%"`
  5. 不是 baseline 且 `fixed_pct_stop` → `"fixed_pct_stop"`
  6. `prefix_ok` 為 False → `"lookahead"`
  通過時 `status="pass"`、`reason = calib.status`。失敗時數字欄位照樣填（有算到的話），Task 6 的 baseline 起點要用 `total_return`。
- `gates` 和 `prepare` 每輪只算一次，`calibrate` 的 5 次回測共用。`seconds` 記 `gates`、`prepare`、`calibrate`、`prefix` 各花幾秒。
- CLI 把整個 `evaluate` 包在 try/except，任何例外都寫成 `status="fail"`、`reason="error: ..."` 的 JSON，exit code 0。

- [ ] **Step 3: 寫最小測試集 `tests/test_ar_score.py`**

```python
def test_calibrate_converges_to_band():          # covers: 比例換算分支→in_band
    run(r) → max_dd = 5r  ⇒ 第 1 次 0.20、第 2 次 r=0.05 → 0.25
    assert c.status == "in_band" and c.runs == 2 and math.isclose(c.r, 0.05)

def test_calibrate_capped_when_mdd_low_at_max_r():  # covers: R_MAX 上限→capped
    max_dd = 2r ⇒ assert status == "capped" and r == 0.05

def test_calibrate_unreachable_and_bankrupt():   # covers: 二分分支→unreachable、bankrupt 出口
    max_dd = 0.20 if r < 0.0445 else 0.30 ⇒ status "unreachable"、runs == 5
    第一次就 bankrupt ⇒ status "bankrupt"、runs == 1

def test_coin_drawdowns_definition_b():          # covers: 排序、連乘、cummax
    A：+10%、−20%、−20%、+5% ⇒ ≈ 0.36；B：−20% ×3 ⇒ ≈ 0.488；交易順序打亂結果不變

def test_fixed_pct_stop_detects_95_percent():    # covers: 四捨五入、佔比門檻（邊界 95%）
    95 筆距離 0.200 + 5 筆不同 ⇒ True；94 + 6 ⇒ False；ATR 型隨機距離 ⇒ False

def test_prefix_check_catches_lookahead():       # covers: 截斷→重算→比對
    2 幣 1h 合成資料、gates 每天都含兩幣
    誠實版 entry = c[i] > c[i-1] ⇒ True
    偷看版 entry = c[i+1] > c[i]（最後一根補 False）⇒ False

def test_yearly_and_coin_share():                # covers: 年度起點值、年內 mdd、佔比
    2 年的 curve：第一年 10000→12000（中間跌到 9000），第二年 12000→10800
    ⇒ [{"year": y1, "return": 0.2, "mdd": 0.25}, {"year": y2, "return": -0.1, ...}]
    coin_profit_share：A +300、B +100、C −50 ⇒ A 0.75、B 0.25（分母 = 正獲利總和）

def test_evaluate_reports_error_and_few_trades():  # covers: reason 1、reason 3、baseline 略過第 5 條
    signals 丟 ValueError ⇒ status "fail"、reason 以 "error: ValueError" 開頭
    只產生 5 筆交易的 candidate ⇒ reason "trades<=100"
```

- [ ] **Step 4: 跑測試**

Run: `pytest tests/test_ar_score.py -q`
Expected: `8 passed`。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/score.py tests/test_ar_score.py
git commit -m "feat(autoresearch): calibration, elimination rules, lookahead check, score CLI"
```

---

### Task 6: 迴圈、agent 指示、報告

**Files:**
- Create: `autoresearch/loop.py`
- Create: `autoresearch/program.md`
- Modify: `.gitignore`（加一行 `autoresearch/runs/`）
- Test: `tests/test_ar_loop.py`

**Interfaces:**
- Consumes: `baselines.BASELINES`、`baselines.variant_source`、`data.load_all`、score CLI（Task 5，用 subprocess 呼叫）
- Produces:
  - `CAND = "strategy/candidate.py"`
  - `COLUMNS = ["round","description","status","reason","r","mdd","total_return","trades","max_coin_dd","win_rate","profit_factor","entry_tf","version","seconds"]`
  - `@dataclass Ctx`：`cwd: Path`、`run_dir: Path`、`run_id: str`、`best_tag: str | None`、`best_score: float`、`baseline_tag: str | None`、`model: str | None`
  - `git(cwd: Path, *args: str) -> str`
  - `require_branch(cwd: Path) -> str`：分支不是 `autoresearch/` 開頭就 `SystemExit`，訊息給出 `git worktree add ../screener-ar -b autoresearch/2026-10-22`
  - `changed_files(cwd: Path) -> set[str]`：`git status --porcelain` 的路徑（不含被 ignore 的）
  - `build_claude_cmd(prompt: str, model: str | None) -> list[str]`
  - `build_prompt(program: str, rows: list[dict], ctx: Ctx) -> str`
  - `run_agent(cmd: list[str], cwd: Path, log_path: Path, timeout: int = 900) -> None`
  - `run_eval(cwd: Path, out: Path, *, baseline: bool, seed: int, timeout: int = 3600) -> dict`：subprocess 跑 score CLI；逾時回 `{"status":"fail","reason":"timeout"}`
  - `append_row(tsv: Path, row: dict) -> None`
  - `top3(rows: list[dict]) -> list[dict]`：`status` 為 `keep` 或 `pass`，依 `total_return` 由大到小取 3 個
  - `write_report(ctx: Ctx, rows: list[dict]) -> Path`：寫 `run_dir/report.md`
  - `round0(ctx: Ctx, evaluator=run_eval) -> list[dict]`
  - `research_round(ctx: Ctx, n: int, agent=run_agent, evaluator=run_eval) -> dict`
  - `main(argv: list[str] | None = None) -> int`：`--rounds`（預設 30）、`--model`

- [ ] **Step 1: 寫 `autoresearch/program.md`**（給 agent 的固定指示，內容照下面）

```markdown
# Autoresearch 指示

你在優化一個加密貨幣 USDT 永續合約的做多策略。每一輪只在 `strategy/candidate.py` 試一個想法，改完就結束。回測、評分、留或丟都由腳本負責，你不用跑回測。

## 規則
1. 只能修改 `strategy/candidate.py`。不要建立、刪除或修改其他檔案。
2. 每輪只試一個想法。把 `DESCRIPTION` 改成一句話說明這個想法（繁體中文，60 字以內）。
3. 介面不能變：`ENTRY_TF`、`SELECT_WINDOW_4H`、`TOP_N`、`select_score(w, cut_ms)`、`btc_gate(btc_close)`、`signals(b)`、`update_stop(sig, i, pos)`。
4. `ENTRY_TF` 只能是 5m、15m、30m、1h、2h、4h、8h。
5. 初始止損（`signals` 回傳的 `stop`）必須用常用指標算出來，例如 ATR、前波低點、均線。不能用「收盤價 × (1 − n%)」。止損必須低於當根收盤價。
6. `update_stop` 回傳持倉中的建議止損。腳本只會往上調，比現有止損低的值會被忽略。
7. 陣列第 i 個位置只能用到第 i 根（含）以前的資料。腳本會截斷資料重跑，偷看未來會被淘汰。
8. 只能使用傳進來的 K 線。不能讀檔、不能連網。
9. 可以 import：`numpy`、`pandas`、`strategy.shotgun_v3`、`strategy.shadow_lists`、`autoresearch.indicators`（`rma`、`rma_last` 很快，優先用）。
10. 不能改：出場邏輯（SMMA10 < SMMA20，由腳本處理）、部位大小、曝險上限。

## 評分（腳本做）
- 期間 2021-01-01 到 2026-10-22。
- 腳本調整每筆風險 r（最多 5%），讓總回撤落在 24%–26%。
- 淘汰：交易 100 筆以下、任一幣種單獨回撤超過 40%、權益歸零、偷看未來、固定百分比止損。
- 通過的版本比總報酬。

## 建議
- 先看下面的歷史紀錄。不要重複已經失敗的想法。
- `select_score` 裡有 5 種現成的選標的邏輯（`SELECTION`），可以修改或組合。
- 5m、15m 時框的回測比較慢。
```

- [ ] **Step 2: 實作 `loop.py`**

- `build_claude_cmd`：`["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Glob,Grep"]`，有 `model` 再加 `["--model", model]`。實作前先跑 `claude --help` 確認這三個旗標存在；不存在就改用 `--help` 列出的等價旗標，並在 commit 訊息註明。
- `run_agent`：stdout／stderr 寫進 `log_path`，**不解析**（poemer Stop hook 會在輸出後面附加文字）。逾時就殺掉 process，當作沒改。
- `build_prompt`：`program.md` 全文 + 目前第 1 名摘要（版本、說明、總報酬、回撤、r、`entry_tf`）+ 最近 15 列的 `round`、`status`、`reason`、`total_return`、`description`。
- `round0`：對每個 `BASELINES`：寫入 `variant_source(sel)` → `run_eval(baseline=True, seed=0)` → commit `"ar r000 {sel}"` → tag `ar/{run_id}/r000-{sel}`（不論通過與否都 tag）→ 寫一列（`status` = `pass`／`fail`）。通過的取 `total_return` 最大者當第 1 名；全都沒通過時，取 `total_return` 最大者當起點、`best_score = -inf`。最後 `git reset --hard <best_tag>`，`baseline_tag = best_tag`。第 1 名那一列的 `status` 改成 `keep`。
- `research_round`：
  ```python
  agent(build_claude_cmd(build_prompt(...), ctx.model), ctx.cwd, run_dir / f"agent_r{n:03d}.log")
  files = changed_files(ctx.cwd)
  if files != {CAND}: 結果 discard，reason = "no change" 或 "touched: <其他檔>"
  else:
      res = evaluator(ctx.cwd, run_dir / f"r{n:03d}.json", baseline=False, seed=n)
      if res["status"] == "pass":
          git commit -m "ar r{n:03d}: {description}"；tag ar/{run_id}/r{n:03d}
          if res["total_return"] > ctx.best_score: status keep，更新 best_tag／best_score
          else: status pass，git reset --hard best_tag
      else: status discard
  discard 時：git checkout -- . && git clean -fd && git reset --hard best_tag
  寫一列、重寫 report.md
  ```
- `write_report`：`top3` 每一名列出版本 tag、說明、總報酬、回撤、r、`entry_tf`、逐年表（從 `r{n}.json` 讀 `yearly`）、獲利最多 3 個幣的佔比、`git diff <baseline_tag> <tag> -- strategy/candidate.py`。`select_window != 300`、`entry_tf != "1h"`、`top_n != 20` 時加一行「部署時實盤也要改成 …」。
- `main`：`require_branch` → `run_id` = 分支名去掉 `autoresearch/` → `run_dir = autoresearch/runs/<run_id>/` → `data.load_all()` 確認資料齊全（缺資料就印「先執行 `python -m autoresearch.data`」並回傳 1，不自動下載）→ `run_dir/results.tsv` 不存在才跑 `round0`（存在就從最後一列接續，`best_tag` 取 `keep` 列中總報酬最大的）→ 跑 `--rounds` 輪。整段包 `try/except KeyboardInterrupt`：`git reset --hard best_tag`、`write_report`、印出報告路徑、回傳 130。

- [ ] **Step 3: 寫最小測試集 `tests/test_ar_loop.py`**

共用 fixture：`tmp_path` 裡 `git init`、建 `strategy/candidate.py` 和 `.gitignore`（含 `autoresearch/runs/`）、commit、切到 `autoresearch/test` 分支。假 `agent` 直接改檔；假 `evaluator` 依劇本回 dict。

```python
def test_build_claude_cmd():                          # covers: model 有／無兩個分支
    assert build_claude_cmd("x", None)[:3] == ["claude", "-p", "x"] and "--model" not in ...
    assert build_claude_cmd("x", "sonnet")[-2:] == ["--model", "sonnet"]

def test_require_branch_rejects_main(git_repo):       # covers: 分支檢查出口
    切回 main ⇒ pytest.raises(SystemExit)

def test_round_flow_keep_pass_discard(git_repo):      # covers: keep／pass／discard 三個出口、tag、reset、tsv
    best_score=5
    r1：agent 改 candidate、evaluator 回 pass 10 ⇒ keep、HEAD == tag r001
    r2：agent 改 candidate、evaluator 回 pass 7 ⇒ pass、tag r002 存在、HEAD 回到 r001
    r3：agent 改 candidate 又新增 other.py ⇒ discard、reason 含 "touched"、other.py 不存在、HEAD 仍是 r001
    r4：agent 什麼都不做 ⇒ discard、reason "no change"、evaluator 沒被呼叫
    assert results.tsv 有 4 列、status 依序 keep, pass, discard, discard

def test_round0_starts_even_if_all_baselines_fail(git_repo):  # covers: 全部 fail 的分支（Review Focus 1）
    evaluator 對 5 個 baseline 都回 fail，total_return 分別 1..5
    assert ctx.best_score == -inf and ctx.best_tag.endswith("r000-martin30")（第 5 個最大）
    assert candidate.py 內容 == variant_source("martin30")

def test_keyboard_interrupt_resets_and_reports(git_repo, monkeypatch):  # covers: Ctrl-C 出口（Review Focus 5）
    agent 改完 candidate 後丟 KeyboardInterrupt
    main(...) 回傳 130、candidate.py 回到 best_tag 的內容、report.md 存在

def test_report_top3_sorted_and_flags(git_repo):      # covers: top3 排序、部署提醒
    5 列（keep/pass/discard 混合）⇒ 報告依序列出總報酬最高的 3 個 keep/pass
    其中一個 select_window=200 ⇒ 報告含 "部署時實盤也要改成 200"
```
`main` 的測試用 monkeypatch 把 `data.load_all`、`round0`、`run_agent` 換成假的。

- [ ] **Step 4: 跑測試**

Run: `pytest tests/test_ar_loop.py -q`
Expected: `6 passed`。

- [ ] **Step 5: 跑全部 autoresearch 測試和既有測試**

Run: `pytest tests/test_ar_bars.py tests/test_ar_data.py tests/test_ar_baseline.py tests/test_ar_engine.py tests/test_ar_score.py tests/test_ar_loop.py tests/test_shotgun_v3.py tests/test_shadow_lists.py -q`
Expected: 全部 passed（既有的兩個檔確認實盤函式沒被碰到）。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/loop.py autoresearch/program.md .gitignore tests/test_ar_loop.py
git commit -m "feat(autoresearch): research loop, agent instructions and top-3 report"
```

---

### Task 7: 合成資料煙霧測試（不連網、不下載）

**Files:** 不新增檔案。合成資料寫在暫存資料夾，不進 repo。發現問題就回到對應的 Task 修。

- [ ] **Step 1: 建立實驗用 worktree**

Run: `git worktree add ../screener-ar -b autoresearch/smoke <harness 所在分支>`
Expected: `git -C ../screener-ar branch --show-current` 印出 `autoresearch/smoke`。

- [ ] **Step 2: 產生合成 5m 資料（3 個幣，2020-01-01～2026-10-22，約 100 MB）**

Run（`AR_CACHE_DIR` 指向暫存資料夾，例如 session scratchpad 底下的 `ar-cache`）：
```bash
cd ../screener-ar && export AR_CACHE_DIR=<暫存資料夾>/ar-cache
python - <<'EOF'
import json, os, numpy as np
from pathlib import Path
from autoresearch import data
from autoresearch.bars import Bars
t = np.arange(data.BTC_FETCH_START_MS, data.END_MS, 300_000, dtype=np.int64)
for k, sym in enumerate(["BTCUSDT", "AAAUSDT", "BBBUSDT"]):
    rng = np.random.default_rng(k)
    c = 100 * np.exp(np.cumsum(rng.normal(2e-6, 0.002, len(t))))
    o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) * 1.001; l = np.minimum(o, c) * 0.999
    v = np.full(len(t), 10.0)
    data.save_5m(sym, Bars(t, o, h, l, c, v, v * c))
Path(os.environ["AR_CACHE_DIR"], "universe.json").write_text(json.dumps({"created": "smoke", "symbols": ["BTCUSDT", "AAAUSDT", "BBBUSDT"]}))
print("ok")
EOF
```
Expected: 印出 `ok`，沒有任何網路請求。

- [ ] **Step 3: 確認缺資料時不會自動下載**

Run: `AR_CACHE_DIR=<暫存資料夾>/empty python -m autoresearch.loop --rounds 0; echo exit=$?`
Expected: 印出「先執行 `python -m autoresearch.data`」，`exit=1`，沒有下載。

- [ ] **Step 4: 跑第 0 輪**

Run: `python -m autoresearch.loop --rounds 0`（沿用 Step 2 的 `AR_CACHE_DIR`）
Expected:
- `autoresearch/runs/smoke/results.tsv` 有 5 列，每列的 `reason` 都不是 `error: ...`。合成資料交易少，`trades<=100` 是正常的淘汰原因。
- `report.md` 存在，`git status --porcelain` 是空的。
- 回報每個 baseline 的 `seconds`。這只代表 3 個幣的速度，不代表 60 個幣。

- [ ] **Step 5: 跑 1 輪真的 agent（先問使用者）**

這一步會呼叫一次 `claude -p`，消耗用量。先問使用者要不要跑。同意才執行：
Run: `python -m autoresearch.loop --rounds 1`
Expected: `results.tsv` 多 1 列、`status` 是 `keep`／`pass`／`discard` 之一、`agent_r001.log` 存在、`git status --porcelain` 是空的。

- [ ] **Step 6: 清理**

Run: `git worktree remove ../screener-ar && git branch -D autoresearch/smoke && git tag -l "ar/smoke/*" | xargs git tag -d`，再刪掉 `<暫存資料夾>/ar-cache`。
Expected: `git worktree list` 只剩原本的 worktree，`git tag -l "ar/smoke/*"` 沒有輸出。

---

## 在實驗主機上執行（不在這次開發範圍）

要真的跑 autoresearch 的主機才做這一段。這台開發主機不做。

1. 建 worktree：`git worktree add ../screener-ar -b autoresearch/<日期> <harness 所在分支>`。
2. 下載資料（約 30–60 分鐘，約 1–2 GB）：`python -m autoresearch.data`。預期最後印出 `61 symbols ready`（BTC 已在池內時是 60）。
3. 抽查重新取樣和 Binance 原生日線一致：
   ```bash
   python - <<'EOF'
   import json, numpy as np
   from pathlib import Path
   from autoresearch import data, bars
   d = bars.resample(data.load_5m("BTCUSDT"), 1440)
   raw = json.loads(Path("data/backtest_cache/klines_BTCUSDT_1d_1000.json").read_text())
   ref = {int(r[0]): [float(x) for x in r[1:6]] for r in raw}
   both = [i for i, t in enumerate(d.t) if int(t) in ref]
   bad = [int(d.t[i]) for i in both if not np.allclose([d.o[i], d.h[i], d.l[i], d.c[i], d.v[i]], ref[int(d.t[i])], rtol=1e-9)]
   print(len(both), "overlap,", len(bad), "mismatch", bad[:5])
   EOF
   ```
   沒有 `klines_BTCUSDT_1d_1000.json` 時，先在主 repo 跑一次 `python tools/backtest_compare.py`。預期 overlap > 500、`0 mismatch`。有 mismatch 就停。
4. 跑第 0 輪：`python -m autoresearch.loop --rounds 0`。單一 baseline 超過 10 分鐘就先停，看 `seconds` 哪一段最慢。
5. 確認第 0 輪合理後，開跑：`python -m autoresearch.loop --rounds 30`。結果在 `autoresearch/runs/<日期>/report.md`。
