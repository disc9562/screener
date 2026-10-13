# Shotgun v3 運行手冊

## 上線前
1. 對帳：`core_backtests\venv\Scripts\python.exe tools/replay_parity.py --research-root C:\Users\jay\Desktop\core_backtests` → 必須 PASS。
2. `.env`：`USE_TESTNET=true`、`BINANCE_TESTNET_API_KEY/SECRET`、Discord webhooks；可調 `NOTIONAL_PCT`（預設 0.04）、`DISASTER_STOP_PCT`（0.20）、`EXCLUDE_SYMBOLS`（BTCDOMUSDT）。
3. 舊的 alligator 倉位檔（data/positions_volume_*.csv）不再使用；v3 寫 `data/positions_shotgun_v3.csv`。

## 啟動
`pm2 start ecosystem.config.js`（main.py 會自行計算當日名單，不需 `--fetch-now`）。

## testnet 期間（2–4 週）每日檢查
- 每天 00:00 UTC 後 Discord 會收到當日名單；BTC 在日線 EMA200 之下時不會開新倉（log 中 `bull=False`）。
- 每日抽查：把前一日的 1h 資料用 `tools/replay_parity.py` 的 `replay_symbol` 離線重算，與 `data/positions_shotgun_v3.csv` 的進出場比對。
- 交易所端每筆多單都應有一張 −20% 的 STOP_MARKET（reduceOnly）。

## 已知落差
市價滑價、資金費率未計；啟動暖機只用 1,500 根 1h，SMMA233 與研究的初值差 < 0.5%，極少數邊界訊號可能不同。
