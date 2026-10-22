# Shotgun v3 運行手冊

> v3.1（分支 `proposal/btc-breadth-gate`）加了持倉上限、「BTC 且寬度」開關與開關關掉就全平，見 `docs/shotgun-v3.1-gate.md`。
> 新的 `.env` 參數：`NOTIONAL_PCT`（預設改為 0.10）、`MAX_POSITIONS`（10）、`BREADTH_ON`（0.5）、`BREADTH_MIN_DAILY_QV`（5000000）。
> testnet 每日多查：每小時 log 有 `Breadth <時間>: <讀數> (<N> liquid symbols, base <4h 收盤>)`；
> 開關改變時 Discord 有「大盤開關 ON／OFF／資料不足」；全平後交易所端部位與止損單都要清空。

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

## 影子記錄（只記錄、不下單；2026-10-18 建立，2026-10-19 依 Codex 審查修正）
- 全部在單一背景佇列執行（不拖主迴圈、只有一個寫入者），用獨立 fetcher（限速額度仍共用）；失敗只記 log，不影響交易。
- `data/shadow_lists.csv`：每天換日後 10 分鐘（避開換日暖機的請求尖峰；重啟時補記、同一天不重複）記錄**全母體**每個幣一列：
  date, symbol, in_rs, rs_rank, in_martin, s1…s24（EMA20 最近 5 根上升 ÷ 5×ATR14）, st1…st24（狀態）, ladder, ladder_complete, r30, martin_ok, martin_status。
  狀態：ok｜fetch_failed｜insufficient｜stale（最後一根不是 cutoff 前剛收完）｜gap（最近 40 根不連續）。缺值不算「沒達標」；
  ladder = 最小在哪個時框 ≥ 0.15，比它小的時框只要有一個不是 ok 就記 None 且 ladder_complete=False。
- `data/shadow_entries.csv`：每筆 Shotgun 進場排背景記錄 time, symbol, signal_close（訊號收盤，不是實際成交價）, paper_opened 與同樣的斜率／狀態欄。
- 每日多一輪 1h（每幣 200 根，權重 2）；每筆進場多 1h 200＋4h 300（權重 4）。
- 2026-10-19 補充：shadow_lists 也記換日時 4h 沒抓到的幣（4h 以上標 fetch_failed）；每時框多記 age（最後一根收完距 cutoff 幾小時）。
  shadow_entries 多記 exchange（filled／disabled）、order_id、fill_price（交易所實際成交均價）。關機時最多等 60 秒把影子佇列做完。
  **只跑一個 bot 實例**（兩個實例會同時寫同一批 CSV 與倉位檔）。

## 交易端行為（2026-10-19 修正）
- 交易所進場失敗（下單錯誤或沒有回應）→ **撤掉剛建的紙上倉位**並在 Discord 警示「交易所進場失敗，紙上不建倉」；下一根若條件仍成立會再試。
  （原本紙上倉會留著，之後平倉會對一個不存在的部位下 reduceOnly 賣單。）
- 進場成功 → positions CSV 記 entry_order_id、entry_fill_price；市價單改用 newOrderRespType=RESULT 取得實際成交均價。
- fetcher 遇到網路錯誤（ConnectionError／Timeout）會重試 3 次（原本被 except 吞掉，重試從沒觸發）；用完照舊回傳「沒資料」，不會讓換日整批失敗。

## 前瞻判定協議（2026-10-19 寫死，看 testnet 結果之前不得修改；三方意見見 core_backtests/docs/slope45_final.md）
1. 起算：影子記錄修正版上線後第一個完整 UTC 日。固定 **180 天**一次判定，中途不看分組績效、不提前停、不延長。
2. 樣本：Shotgun **完成交易 ≥ 200 筆**，且「進場 s1 ≥ 0.15」與「< 0.15」兩組各 ≥ 100 筆；只算 st1 = ok 的交易。不足 = 證據不足（不是沒效）。
3. 主檢定（兩項都要過才可考慮當過濾器）：
   (a) 進場 s1 與該筆淨報酬的等級相關（Spearman）> 0；
   (b) s1 ≥ 0.15 組的整筆 PF 高於 s1 < 0.15 組。
   θ 只用 0.15（歷史上看過才定的，這是第一次用新資料驗）；不准試其他 θ、上下界或「陡但不要太陡」。
4. 另報（不當門檻）：前 5% 交易佔淨利、幣別集中度、各時框 ladder 分組、資料完整率（st=ok 比例要 ≥ 99%，否則先判不可評）。
5. Martin30 vs RS：分開判，不和斜率混成一個故事；用記錄的每日名單，事後下載歷史 K 線以研究引擎離線重播。
6. 通過也只支持「繼續影子驗證或小規模試行」，不自動改 Shotgun 進場；任何新定義（例如固定日曆窗口的斜率）要另開新觀察期。
