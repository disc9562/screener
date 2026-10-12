# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-7
# Title: 實作交易與市場動態通知
# Status: Completed
# Points: 5 (預估值)
# Priority: Medium
# Owner: Bob (Scrum Master)

## 使用者故事 (User Stories)

**Story 1: 交易事件通知**
> 作為一個**交易員**，
> 我想要**在交易被系統自動開倉或平倉時，能夠收到即時的 Discord 通知**，
> 以便**我能隨時掌握系統的交易動態，而無需持續監控日誌**。

**Story 2: 強勢標的異動通知**
> 作為一個**分析師**，
> 我想要**在強勢標的列表更新時，收到一份包含「新增」與「移除」標的的摘要通知**，
> 以便**我能快速了解市場的輪動情況**。

## 驗收標準 (Acceptance Criteria)

1.  **AC1: 開倉通知**
    *   當 `PositionManager` 成功開立一個新倉位時，必須發送一條 Discord 通知。
    *   通知內容必須清晰地包含：交易對 (Symbol), 操作 (BUY), 進場價格, 以及倉位大小 (Units)。

2.  **AC2: 平倉通知**
    *   當 `PositionManager` 因「止盈 (TAKE_PROFIT)」或「止損 (STOP_LOSS)」而關閉一個倉位時，必須發送一條 Discord 通知。
    *   通知內容必須清晰地包含：交易對 (Symbol), 操作 (SELL), 平倉價格, 平倉原因, 以及這次交易的盈虧 (PnL)。

3.  **AC3: 強勢標的異動通知**
    *   當 `main_orchestrator` 偵測到 `new_strong_targets` 或 `targets_no_longer_strong` 列表不為空時，必須發送一條 Discord 通知。
    *   通知內容必須清晰地列出被「新增」和「移除」的標的。

4.  **AC4: 通知格式**
    *   通知應使用 Discord 的「嵌入 (Embed)」格式，以顏色區分事件類型（例如，開倉用綠色，平倉用紅色，列表異動用藍色）。
    *   訊息應結構化，易於閱讀。

5.  **AC5: 設定檔驅動**
    *   Discord Webhook 的 URL 必須從設定檔 (`config.py` 或 `.env`) 中讀取，不得寫死在程式碼中。

6.  **AC6: 開倉通知包含止損價格**
    *   當 `PositionManager` 發送開倉通知時，通知內容必須清晰地包含止損價格 (Stop-Loss Price)。

## 開發任務 (Tasks / Subtasks)

-   **Task 1: 建立通知服務 (Notification Service)**
    -   [x] 1.1 在 `services/` 目錄下建立一個新檔案 `notification_service.py`。
    -   [x] 1.2 在檔案中建立一個 `NotificationService` 類別，其 `__init__` 方法接收 webhook URL 作為參數。
    -   [x] 1.3 實作一個通用的 `send_embed_notification(embed)` 方法。
    -   [x] 1.4 實作 `format_trade_notification(symbol, action, price, units, reason, pnl, stop_loss_price=None)` 方法，用於建立交易通知的 Embed 物件。
    -   [x] 1.5 實作 `format_list_change_notification(added, removed)` 方法，用於建立強勢標的列表異動的 Embed 物件。

-   **Task 2: 整合通知服務到倉位管理器**
    -   [x] 2.1 修改 `services/position_manager.py`，在 `__init__` 方法中初始化 `NotificationService`。
    -   [x] 2.2 修改 `open_position` 方法：在成功開倉後，格式化並呼叫通知服務，並傳遞止損價格。
    -   [x] 2.3 修改 `update_positions` 方法：在成功平倉後，格式化並呼叫通知服務。

-   **Task 3: 整合通知服務到主協調器**
    -   [x] 3.1 修改 `main.py`，在 `main_orchestrator` 中初始化 `NotificationService`。
    -   [x] 3.2 在計算出 `new_strong_targets` 和 `targets_no_longer_strong` 後，如果列表不為空，則呼叫通知服務發送異動通知。

-   **Task 4: 撰寫單元測試**
    -   [x] 4.1 建立 `tests/test_notification_service.py`。
    -   [x] 4.2 使用 `pytest-mock` 來模擬 `DiscordWebhook` 的 `execute` 方法，驗證兩種通知 (`trade` 和 `list_change`) 都能被正確格式化並呼叫。

## 開發者備註 (Dev Notes)

*   **職責分離**: `NotificationService` 的職責應保持單純，只負責「發送格式化訊息」。所有關於「何時」發送以及「發送什麼內容」的決策，應由 `PositionManager` 和 `main_orchestrator` 來處理。

*   **訊息格式化**: 建議在 `NotificationService` 中建立多個私有方法來處理不同類型的 Embed 物件建立，讓主方法更清晰。

*   **現有依賴**: `discord-webhook` 函式庫已存在於 `requirements.txt` 中，可以直接使用。

## File List

**Created**:
*   `docs/stories/story-7-discord-notifications.md`
*   `services/notification_service.py`
*   `tests/test_notification_service.py`

**Modified**:
*   `services/position_manager.py`
*   `main.py`
