# Autoresearch 指示

你在優化一個加密貨幣 USDT 永續合約的做多策略。每一輪只在 `strategy/candidate.py` 試一個想法，改完就結束。回測、評分、留或丟都由腳本負責，你不用跑回測。

## 規則
1. 只能修改 `strategy/candidate.py`。不要建立、刪除或修改其他檔案。
2. 每輪只試一個想法。把 `DESCRIPTION` 改成一句話說明這個想法（繁體中文，60 字以內）。
3. 介面不能變：`ENTRY_TF`、`SELECT_WINDOW_4H`、`TOP_N`、`select_score(w, cut_ms, btc_w)`、`btc_gate(btc_close)`、`signals(b, aux)`、`update_stop(sig, i, pos)`。
4. `ENTRY_TF` 只能是 5m、15m、30m、1h、2h、4h、8h。
5. 初始止損（`signals` 回傳的 `stop`）必須用常用指標算出來，例如 ATR、前波低點、均線。不能用「收盤價 × (1 − n%)」。止損必須低於當根收盤價。
6. `update_stop` 回傳持倉中的建議止損。腳本只會往上調，比現有止損低的值會被忽略。
7. 陣列第 i 個位置只能用到第 i 根（含）以前的資料。腳本會截斷資料重跑，偷看未來會被淘汰。
8. 只能使用傳進來的資料，全部唯讀：`b`（該幣 `ENTRY_TF` K 線）；`aux["base5m"]`、`aux["btc5m"]`（該幣與 BTCUSDT 的 5m，和 `b` 截在同一個結束時間，可用 `autoresearch.bars.resample` 轉時框，對齊自己處理：第 i 根只能用收盤時間 ≤ `b.t[i]` + 時框的 5m）；`w`、`btc_w`（該幣與 BTCUSDT 在 cutoff 前的 4h）；`btc_close`（BTC 日線收盤）。不能讀檔、不能連網。
9. 只能 import：`numpy`、`pandas`、`math`、`strategy.shotgun_v3`、`strategy.shadow_lists`、`autoresearch.indicators`（`rma`、`rma_last` 很快，優先用）、`autoresearch.bars`、`config`。有點號的模組要寫成 `from a import b` 或 `import a.b as y`。
10. 下面這些會讓這輪直接失敗（原因 `forbidden: ...`，不跑回測）：其他 import、相對 import、`from x import *`、裸 `import a.b`；`open`、`exec`、`eval`、`compile`、`globals`、`locals`、`vars`、`getattr`、`setattr`、`delattr`、`input`、`breakpoint`；任何 `__xxx__` 名稱；對屬性賦值或刪除（`x.y = ...`、`del x.y`，`update_stop` 請用回傳值，不要改 `pos`）；`.setflags`、`.flags`、`.base`、`.ctypes`；`.os`、`.sys` 這類模組屬性與 `np.load`、`pd.read_*` 這類讀檔函式；修改 import 進來的容器（`config.X.append(...)`、`TF_MINUTES["3h"] = ...`、`X += [...]`）。評分後也會比對 `config`、`autoresearch.bars`、`autoresearch.indicators`、`strategy.shotgun_v3`、`strategy.shadow_lists` 的模組資料，有變就失敗（原因 `forbidden: harness modified`）。快取請放在 `candidate.py` 自己的模組變數裡。
11. `signals()` 回傳的每個值都必須是長度 n（= `len(b)`）的 numpy 陣列。
12. 不能改：出場邏輯（SMMA10 < SMMA20，由腳本處理）、部位大小、曝險上限。

## 評分（腳本做）
- 期間 2021-01-01 到 2026-10-22。
- 腳本調整每筆風險 r（最多 5%），讓總回撤落在 24%–26%。
- 淘汰：交易 100 筆以下、任一幣種單獨回撤超過 40%、權益歸零、偷看未來、固定百分比止損。
- 通過的版本比總報酬。

## 建議
- 先看下面的歷史紀錄。不要重複已經失敗的想法。
- `select_score` 裡有 5 種現成的選標的邏輯（`SELECTION`），可以修改或組合。
- 5m、15m 時框的回測比較慢。
