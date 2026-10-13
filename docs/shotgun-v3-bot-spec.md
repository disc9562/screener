# Shotgun v3 上 bot — 規格（2026-10-14，待使用者審閱）

研究依據（core_backtests repo）：`docs/shotgun_wide_results.md`（§10.14）、`docs/shotgun_trend_parts_results.md`（§10.16）。
策略 = 定案配方 ＋ BTC 日線 EMA200 開關 ＋ 每筆名目 4%。回測（名目 4%）：樣本內年化 32.3%／回撤 −20.6%，樣本外年化 34.8%／回撤 −15.0%。
**bot 的交易邏輯必須和回測逐筆一致**；任何偏差都要先在「重播對帳」裡被抓到才上 testnet。

## 一、策略規則（照回測，不得改動）

全部時間為 UTC；K 棒以開盤時間標示，「某根收盤」= 開盤時間 + 1 根。只做多，同一檔同時最多一倉，不加碼。

**每日 00:00 UTC（日 K 收完後）**
1. 母體：`get_all_symbols()`（TRADING 的 USDT 永續），排除代幣化股票／商品（沿用研究的 EQUITY_LIKE 名單）與 `EXCLUDE_SYMBOLS`。
2. RS 名單（舊公式，= bot 現用 `relative_strength.py` 的公式，參數改）：每檔抓 4h K 線（limit 300，權重 2），
   取開盤時間 < 00:00 的最後一根為 end，**往前跳過 6 根（1 天）**得 e，用 e 往前 168 根（28 天）計算：
   `w_j = [((c−m30)+(c−m45)+(c−m60))·(j·28/168 + 1) + (m30−m45)+(m30−m60)+(m45−m60)] / m60`，`score = Σ w_j·j`（j=0 最舊…167 最新），
   需 e+1 ≥ 168+60；分數前 20 名 = 當日名單。
3. BTC 開關：BTC 日收盤（剛收完的那天）> 日收盤的 EMA200（`ewm(span=200, adjust=False)`，至少 1,000 天歷史暖機）→ 當日為「多頭日」。
4. 名單與開關**適用於「開盤時間落在當日」的 1h K 棒**（23:00 開的那根仍屬前一日）。

**每根 1h 收盤（合約 kline_1h 收完的那根），對「當日名單 ∪ 有持倉」的幣**
- 指標：SMMA10/20/50/233 = 收盤的 RMA（`alpha = 1/n`）；ER50 = (close − close[50]) ÷ Σ|Δclose|（50 根）。
- **出場**：有持倉且 SMMA10 由 ≥ 轉為 < SMMA20（這根收盤）→ 立刻市價全平。
- **進場**：無持倉、在當日名單、當日為多頭日、SMMA10>20>50>233、close>SMMA10、ER50 ≥ 0.2 → 立刻市價買入，
  名目 = 權益 × `NOTIONAL_PCT`（預設 4%）。
- **災難止損**（回測沒有，見決策 A）：進場後在交易所掛 STOP_MARKET＝進場價 ×（1 − `DISASTER_STOP_PCT`），預設 20%；
  出場時一併撤單。回測影響：觸發 3.1%、全期報酬 +391% → +374%、回撤不變。

**暖機**：啟動時與新幣進名單時，抓 1h K 線 limit 1500（權重 10；名單約 20 檔 + 持倉，總計 ≤ ~300 權重），
用全部 1500 根初始化 RMA（SMMA233 初值偏差 < 0.2%）。

## 二、工程改動（screener repo，分支 `feat/shotgun-v3`）

| 檔案 | 改動 |
|---|---|
| `strategy/shotgun_v3.py`（新） | **純函式、不碰 I/O**：RS 分數（4h）、BTC 開關、SMMA/ER 狀態更新、進出場判斷。bot 與重播對帳共用同一份 |
| `main.py` | 兩組鱷魚（volume_on/off）換成單一 shotgun_v3；排程改成每日 00:00 UTC（取代 08:00/20:00 本地時間） |
| `services/websocket_manager.py` | 改用**合約** kline stream（binance-connector `UMFuturesWebsocketClient`），週期 1h |
| `services/position_manager.py` | 加「名目比例下單」與「訊號出場」（不再用 TP/SL 碰價出場）；災難止損走既有 `place_stop_loss_order` |
| `config.py` | `NOTIONAL_PCT=0.04`、`DISASTER_STOP_PCT=0.20`、`EXCLUDE_SYMBOLS`、RS 參數（4h/28 天/跳 1 天/前 20）、`ER_MIN=0.2` |

REST 一律經 `CryptoFetcher`（已含權重閘門）。每日 RS 計算約 525 檔 × 權重 2 ≈ 1,050，分散在閘門內，不會觸發 ban。

## 三、驗證（上 testnet 之前必過）

1. **單元測試**：RS 分數與研究 `rs_formula_variants._scores("skip")` 對拍；RMA／ER 與研究引擎一致；進出場判斷手造案例。
2. **重播對帳**：用 core_backtests 的歷史 1h 資料，把 `strategy/shotgun_v3.py` 逐根餵進去（名單與開關也由同一份資料重算），
   產生的交易與研究 `results/trend_parts_events.parquet`（arm = BASE_R）比對：抽 30 檔 × 2024 全年，**進出場時間與價格 100% 一致**才算過。
3. **testnet 2–4 週**：每日把 bot 的訊號記錄，與當日收盤後離線重算的訊號比對；不一致就停下查原因。

## 四、使用者決定（2026-10-14 已定）

- A. 災難止損：**−20%**（交易所端 STOP_MARKET）。
- B. **排除** BTCDOM 等指數型合約（`EXCLUDE_SYMBOLS = ["BTCDOMUSDT"]`；回測裡 BTCDOM 37 筆）。
- C. 倉位：**名目 4%**。同時最多約 28 檔，總曝險可能達 112%，需要約 1.1 倍保證金。
- D. 研究線索（4h 雙頂回踩、MA 糾結＋突兀量）的模擬帳戶：**這一階段不做**，主策略在 testnet 跑順、對帳通過後再加。

## 五、已知落差（回測 vs 實盤）

市價成交有滑價（回測只算 0.05%/邊手續費）；資金費率未計（研究實測每筆約 0.043%）；
回測用已實現權益算回撤，實盤浮虧會更深；母體在回測只含現存合約（倖存者偏差）。
