# Shotgun v3 運行手冊

## 上線前
1. 對帳：`core_backtests\venv\Scripts\python.exe tools/replay_parity.py --research-root C:\Users\jay\Desktop\core_backtests` → 必須 PASS。
2. `.env`：`USE_TESTNET=true`、`BINANCE_TESTNET_API_KEY/SECRET`、Discord webhooks；可調 `NOTIONAL_PCT`（預設 0.04）、`DISASTER_STOP_PCT`（0.20）、`EXCLUDE_SYMBOLS`（BTCDOMUSDT）。
3. 舊的 alligator 倉位檔（data/positions_volume_*.csv）不再使用；v3 寫 `data/positions_shotgun_v3.csv`。
4. **先清空 testnet 帳戶**：舊鱷魚 bot 留下的部位與掛單（含非 reduceOnly 的止損／止盈）會和 v3 的部位合併。
   在 testnet 網頁上撤銷所有掛單（一般單＋條件單／algo 單）並平掉所有部位，再啟動 v3。
5. **帳戶模式**：持倉模式必須是「單向」（hedge 雙向模式下 bot 會自動停用下單並在 log 報錯）；
   保證金模式 bot 會在每個幣第一次進場前自動改成「全倉」，槓桿沿用交易所預設。

## 啟動
`pm2 start ecosystem.config.js`（main.py 會自行計算當日名單，不需 `--fetch-now`）。

## testnet 第一天必查（未在開發機驗證過的部分）
- **跨 00:00 UTC（台灣 08:00）換日**：log 出現 `Rebalance <當日>` 與 `Daily rebalance <當日> bull=... list=[...]`；
  23:00 那根（台灣 07:00 開盤）照常處理，沒有 Traceback。換日失敗會在 Discord 收到「名單計算失敗，5 分鐘後重試」。
- **收得到資料**：每個整點後，log 對每個訂閱的幣都有 `Queuing closed k-line for <SYMBOL>`。
- **斷線重連**：Binance 每 24 小時強制斷線一次；log 會出現 `WebSocket dead or silent > 90s. Reconnecting...`，之後恢復收資料。

## testnet 期間（2–4 週）每日檢查
- 每天 00:00 UTC 後 Discord 會收到當日名單；BTC 在日線 EMA200 之下時不會開新倉（log 中 `bull=False`）。
- 每日抽查：把前一日的 1h 資料用 `tools/replay_parity.py` 的 `replay_symbol` 離線重算，與 `data/positions_shotgun_v3.csv` 的進出場比對。
- 交易所端每筆多單都應有一張 −20% 的 STOP_MARKET（reduceOnly）。

## 已知落差
市價滑價、資金費率未計；啟動暖機只用 1,500 根 1h，SMMA233 與研究的初值差 < 0.5%，極少數邊界訊號可能不同。
