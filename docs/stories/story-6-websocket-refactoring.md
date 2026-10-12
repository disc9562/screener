# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-6
# Title: [Technical Story] 重構數據獲取機制以使用 WebSocket
# Status: Completed
# Points: 8 (預估值)
# Priority: High
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story)

> 作為一個**系統維護者**，
> 我想要**將現有的數據獲取機制重構為使用 `binance-connector` 的 WebSocket 即時數據流**，
> 以便**能從根本上解決 API 請求頻率過高的問題，並更高效、更可靠地接收市場數據**。

## 驗收標準 (Acceptance Criteria)

1.  **AC1: 依賴已更新**
    *   `binance-connector` 函式庫必須被新增到 `requirements.txt` 中。

2.  **AC2: WebSocket 管理器已建立**
    *   必須建立一個新的 `services/websocket_manager.py` 檔案，其中包含一個 `WebSocketManager` 類別。
    *   `WebSocketManager` 必須能夠使用 `binance-connector` 來連接到 Binance 的 K 線 WebSocket 端點。
    *   它必須能夠動態地訂閱 (subscribe) 和取消訂閱 (unsubscribe) 任何交易對的 15 分鐘 K 線數據流。

3.  **AC3: 主流程已重構為事件驅動模式**
    *   `main.py` 必須在啟動時，初始化 `WebSocketManager` 並在一個背景執行緒中運行它。
    *   `main_orchestrator` 不再透過迴圈和 `sleep` 來獲取數據，而是從 `WebSocketManager` 提供的數據佇列 (queue) 或透過回呼 (callback) 機制來接收新的 K 線數據。

4.  **AC4: 策略與倉位管理已適配**
    *   `PositionManager` 的 `update_positions` 方法必須能夠處理來自 WebSocket 的新 K 線數據。
    *   `AlligatorStrategy` 必須被調整，使其能夠分析由主流程傳遞過來的即時數據，而不是自己觸發數據獲取。

5.  **AC5: 系統穩定性已驗證**
    *   在一次持續運行的冒煙測試中，系統必須不再出現因請求頻率過高而導致的 `APIError(code=-1003)` 錯誤。

## 開發任務 (Tasks / Subtasks)

-   **Task 1: 更新專案依賴 (AC1)**
    -   [x] 1.1 將 `binance-connector` 新增到 `requirements.txt` 檔案中。
    -   [x] 1.2 執行 `pip install -r requirements.txt` 以確保新依賴可被安裝。

-   **Task 2: 實作 WebSocket 管理器 (AC2)**
    -   [x] 2.1 建立 `services/websocket_manager.py` 及 `WebSocketManager` 類別。
    -   [x] 2.2 實作 `start_stream` 方法，用於初始化 WebSocket 連線並在背景執行緒中運行。
    -   [x] 2.3 實作 `subscribe` 和 `unsubscribe` 方法，允許動態增減要監聽的交易對。
    -   [x] 2.4 實作一個內部佇列 (internal queue)，用於存放從 WebSocket 收到的 K 線數據。
    -   [ ] 2.5 (可選) 實作自動重連機制，以應對網路中斷。

-   **Task 3: 重構主協調器 (AC3)**
    -   [x] 3.1 修改 `main.py`，在啟動時初始化 `WebSocketManager` 並啟動其背景執行緒。
    -   [x] 3.2 移除 `main_orchestrator` 中手動獲取 K 線數據的迴圈和 `sleep` 邏輯。
    -   [x] 3.3 建立一個新的迴圈或回呼函式，用於從 `WebSocketManager` 的佇列中讀取數據，並將其分派給後續的處理器。

-   **Task 4: 適配現有模組 (AC4)**
    -   [x] 4.1 修改 `main.py`，使其在收到 WebSocket 數據後，能將數據傳遞給 `PositionManager` 的 `update_positions` 方法。
    -   [x] 4.2 修改 `main.py`，使其能將數據傳遞給 `AlligatorStrategy` 的 `run_with_existing_data` 方法進行分析。

-   **Task 5: 撰寫整合與壓力測試 (AC5)**
    -   [x] 5.1 建立一個新的測試檔案 `tests/test_integration.py`。
    -   [x] 5.2 撰寫一個測試，模擬長時間運行（例如 5 分鐘），並訂閱大量的交易對，以驗證系統不會再出現 API 速率限制錯誤。

## 開發者備註 (Dev Notes)

*   **核心架構變更**: 這是從「輪詢 (Polling)」模型到「事件驅動 (Event-Driven)」模型的重大轉變。`WebSocketManager` 將成為系統數據流的核心。請確保它的設計是穩健且執行緒安全的。

*   **技術選型**: 我們已決定採用 `binance-connector` 函式庫。請參考其官方文件來實作 K 線數據流 (`kline` 或 `aggTrade` stream) 的訂閱。

*   **資料格式**: 從 WebSocket 收到的數據是 JSON 格式，需要將其轉換為與我們現有 `transformer` 模組輸出一致的 pandas DataFrame 或 Series 格式，才能被現有策略和倉位管理器使用。

*   **錯誤處理**: WebSocket 連線可能會中斷。一個生產級的 `WebSocketManager` 應該包含自動重連的邏輯。

## File List

**Created**:
*   `docs/stories/story-6-websocket-refactoring.md`
*   `services/websocket_manager.py`
*   `tests/test_integration.py`

**Modified**:
*   `requirements.txt`
*   `main.py`
*   `services/position_manager.py` (可能需要微調以適應新的數據流)
*   `strategy/alligator_strategy.py` (可能需要微調以適應新的數據流)
