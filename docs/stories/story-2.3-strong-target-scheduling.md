# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-2.3
# Title: 強勢標的抓取排程與手動觸發
# Status: Approved
# Points: 待估計
# Priority: Medium
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story):
作為一個**交易員**，
我想要**能夠設定強勢標的抓取的時間排程，並在測試環境中可以手動觸發即時抓取**，
以便**我可以確保在特定時間點獲取最新數據，同時在開發和測試時保持靈活性**。

## 驗收標準 (Acceptance Criteria):

1.  **AC1: 定時抓取**
    *   系統應配置為每天早上 8 點和晚上 8 點自動觸發強勢標的抓取。
2.  **AC2: 手動觸發**
    *   應提供一種機制，允許在非排程時間手動觸發強勢標的抓取（主要用於測試和開發）。
3.  **AC3: 部署與測試分離**
    *   排程邏輯應在部署環境中生效，而手動觸發機制應主要用於開發和測試環境。
4.  **AC4: 日誌記錄**
    *   每次強勢標的抓取（無論是排程還是手動觸發）都應有清晰的日誌記錄。

## 開發者備註 (Dev Notes):

*   **Previous Story Insights**: Story 2.2 實作了多策略通知分流。現有的 `main.py` 中有每 4 小時執行一次的週期性掃描邏輯，需要移除或整合。
*   **File Locations**:
    *   `main.py`: 需要修改以實作排程邏輯和手動觸發機制。
    *   `config.py`: 需要新增排程時間的配置。
    *   `.env.example`: 需要更新以包含新的環境變數範例。
*   **Scheduling Logic**: 可以考慮使用 `datetime` 模組進行時間檢查，或引入輕量級的排程庫（例如 `schedule` 或 `APScheduler`，但需評估其複雜性）。優先使用內建模組以減少依賴。
*   **Manual Trigger**: 建議使用命令列參數（例如 `--fetch-now`）來實現手動觸發，這在測試環境中更為靈活。
*   **Deployment Separation (AC3)**: 可以透過檢查環境變數（例如 `ENV=production`）來區分部署環境和測試環境，或者讓手動觸發參數在部署環境中無效。

## 開發任務 (Tasks / Subtasks):

*   **Task 1: 定義排程時間配置**
    *   [ ] 1.1 修改 `config.py`，新增 `TARGET_FETCH_TIMES` 配置，定義強勢標的抓取的排程時間（例如 `["08:00", "20:00"]`）。
    *   [ ] 1.2 更新 `.env.example` 檔案。
*   **Task 2: 實作排程抓取邏輯**
    *   [ ] 2.1 修改 `main.py`，移除現有的 4 小時週期抓取邏輯。
    *   [ ] 2.2 在 `main.py` 中實作排程邏輯，確保每天在 `TARGET_FETCH_TIMES` 設定的時間點觸發強勢標的抓取。
*   **Task 3: 實作手動觸發機制**
    *   [ ] 3.1 修改 `main.py`，新增一個命令列參數（例如 `--fetch-now`），用於手動觸發強勢標的抓取。
    *   [ ] 3.2 如果 `--fetch-now` 參數存在，則在啟動時立即執行一次強勢標的抓取，並忽略排程。
*   **Task 4: 撰寫單元測試**
    *   [ ] 4.1 撰寫測試案例，驗證排程邏輯在特定時間點觸發抓取。
    *   [ ] 4.2 撰寫測試案例，驗證手動觸發機制能正確執行抓取。
*   **Task 5: 更新文件與建置腳本**
    *   [ ] 5.1 更新 `README.md`，說明新的排程和手動觸發功能。
    *   [ ] 5.2 更新 `Makefile`，新增或修改相關的建置/運行指令（如果需要）。

## File List

**Modified**:
*   `main.py`
*   `config.py`
*   `.env.example`
*   `README.md`
*   `Makefile`
