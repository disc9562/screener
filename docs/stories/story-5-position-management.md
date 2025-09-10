# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-5
# Title: 模擬倉位與盈虧管理
# Status: Completed
# Points: 8 (預估值)
# Priority: High
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story)

> 作為一個**交易員**，
> 我需要一個**能夠根據策略訊號，自動化管理模擬倉位的系統**，
> 以便**我能準確追蹤每一筆交易的進場、出場、狀態以及即時的浮動盈虧(PnL)**。

## 驗收標準 (Acceptance Criteria)

1.  **AC1: 倉位記錄可持續化**
    *   系統必須在 `data/positions.csv` 檔案中儲存和讀取所有倉位資訊。
    *   如果檔案不存在，系統應能自動建立它。
    *   CSV 至少應包含以下欄位: `symbol`, `status` (OPEN/CLOSED), `entry_price`, `stop_loss`, `take_profit`, `units`, `entry_timestamp`, `exit_timestamp`, `exit_reason` (e.g., TAKE_PROFIT, STOP_LOSS), `pnl`。

2.  **AC2: 可根據訊號建立新倉位**
    *   當收到一個**新的**「BUY」訊號，且該標的**目前沒有**開放倉位時，系統必須在 `positions.csv` 中新增一筆 `status` 為 `OPEN` 的記錄。
    *   如果該標的已存在開放倉位，則應忽略新的「BUY」訊號，並記錄一條日誌。

3.  **AC3: 可根據市場價格更新並關閉倉位**
    *   在每個新的 15 分鐘 K 棒週期，系統必須遍歷所有 `OPEN` 的倉位。
    *   系統必須根據我們討論的**「K 棒內價格行為模擬」**邏輯，檢查前一根 K 棒的 `High` 和 `Low` 價格，來判斷是否觸發了止盈或止損。
    *   如果觸發，倉位的 `status` 必須更新為 `CLOSED`，並記錄 `exit_timestamp`, `exit_reason` 和最終的 `pnl`。

4.  **AC4: 可計算浮動盈虧 (Floating PnL)**
    *   對於所有尚未平倉的 (`OPEN`) 部位，系統必須能夠根據最新的收盤價，計算並記錄其即時的浮動盈虧。

## 開發任務 (Tasks / Subtasks)

-   **Task 1: 建立倉位管理器 (Position Manager)**
    -   [x] 1.1 在 `services/` 目錄下建立一個新檔案 `position_manager.py`。 [Source: brownfield-architecture.md#程式碼組織與標準]
    -   [x] 1.2 在檔案中建立一個名為 `PositionManager` 的類別，負責所有倉位相關的操作。
    -   [x] 1.3 `PositionManager` 的 `__init__` 方法應能讀取 `data/positions.csv` 的現有倉位到記憶體中 (例如一個 pandas DataFrame)。

-   **Task 2: 實作倉位讀寫 (AC1)**
    -   [x] 2.1 在 `PositionManager` 中實作一個 `_write_positions_to_csv` 的私有方法，用於將記憶體中的倉位數據寫回 `positions.csv`。
    -   [x] 2.2 確保檔案讀寫操作是安全的，能處理檔案不存在或為空的情況。

-   **Task 3: 實作倉位操作 (AC2, AC3)**
    -   [x] 3.1 實作 `open_position(signal)` 方法，該方法接收一個來自策略的訊號 dictionary，檢查是否已有倉位，若無則新增一筆記錄。
    -   [x] 3.2 實作 `update_positions(latest_klines)` 方法，該方法接收一個包含所有活躍標的最新 K 線數據的 dictionary。
    -   [x] 3.3 在 `update_positions` 中，遍歷所有開放倉位，並根據「K 棒內價格行為模擬」邏輯，檢查止盈/止損條件並平倉。

-   **Task 4: 實作盈虧計算 (AC4)**
    -   [x] 4.1 在 `update_positions` 方法中，為所有未被平倉的部位，根據傳入的最新 K 線收盤價，計算其浮動 PnL。
    -   [x] 4.2 將 PnL 的更新結果記錄在日誌中。

-   **Task 5: 整合到主協調器 (AC2, AC3, AC4)**
    -   [x] 5.1 修改 `main.py`，在啟動時初始化 `PositionManager`。
    -   [x] 5.2 當 `AlligatorStrategy` 產生訊號時，呼叫 `position_manager.open_position()`。
    -   [x] 5.3 在主協調器的迴圈中，定期呼叫 `position_manager.update_positions()` 來更新所有倉位的狀態和 PnL。

-   **Task 6: 撰寫單元測試**
    -   [x] 6.1 建立 `tests/test_position_manager.py`。
    -   [x] 6.2 撰寫測試案例，驗證開倉、平倉（止盈/止損）、以及 PnL 計算的邏輯是否正確。

## 開發者備註 (Dev Notes)

*   **核心模組**: `PositionManager` 是這個故事的核心。它應該是唯一負責直接讀寫 `positions.csv` 的模組，以確保資料一致性。

*   **K 棒內價格行為模擬**: 請務必遵循我們討論過的平倉邏輯：**對於每一根新 K 線，先檢查前一根的最高價是否觸及止盈，再檢查最低價是否觸及止損**。這個順序很重要，可以避免在價格劇烈波動時，出現不合理的穿價成交模擬。

*   **無狀態策略**: 我們的 `AlligatorStrategy` 現在是無狀態的，它只負責產生訊號。所有關於「是否已持倉」的判斷，都應該由 `PositionManager` 來處理。

*   **資料依賴**: `PositionManager` 的 `update_positions` 方法需要獲取市場上所有活躍倉位的最新 K 線數據。`main.py` 中的主協調器需要負責提供這些數據。

## File List

**Created**:
*   `docs/stories/story-5-position-management.md`
*   `services/position_manager.py`
*   `tests/test_position_manager.py`
*   `data/positions.csv`

**Modified**:
*   `main.py`


## QA Results

**Reviewer:** Quinn (Test Architect & Quality Advisor)
**Date:** 2025-09-08
**Status:** ✅ PASS

### Summary:
The implementation of the `PositionManager` is excellent. The code is clean, well-structured, and adheres to the principle of separation of concerns by encapsulating all position state management. The unit test coverage is comprehensive and validates all core requirements effectively.

The integration with the main orchestrator is logical. The primary risks associated with this module are related to the data pipeline's stability (rate-limiting), which was noted in the review for Story 4. The module itself is considered high quality.

### Key Findings:

| Category | Status | Comments |
|---|---|---|
| **Requirements Traceability** | ✅ PASS | All Acceptance Criteria are fully met and validated by specific unit tests. |
| **Code Quality** | ✅ PASS | The code is clean, well-documented, and logically structured. The use of a dedicated service for position management is a strong architectural choice. |
| **Testing** | ✅ PASS | The unit tests are comprehensive, covering opening, closing (TP/SL), and PnL update logic correctly. |
| **Risk Assessment** | ✅ PASS | The module itself presents low risk. Its successful operation is dependent on the data quality and availability from the upstream fetchers. |

### Recommendations:
*   No immediate recommendations for this module. The code is approved. The previously noted risks in the data pipeline should be addressed separately.