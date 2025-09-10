# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-4
# Title: 15 分鐘線圖交易邏輯實作 (鱷魚策略)
# Status: Completed
# Points: 5 (預估值)
# Priority: High
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story)

> 作為一個**策略開發者**，
> 我想要**在系統中實作一個基於 15 分鐘 K 線的「鱷魚策略」**，
> 以便**能夠自動產生精確的「開倉」與「平倉」交易訊號，並為後續的倉位管理提供依據**。

## 驗收標準 (Acceptance Criteria)

1.  **AC1: 策略指標可正確計算**
    *   系統必須能夠針對給定的 15 分鐘 K 線數據，正確計算 10、20、50、233 週期的 SMMA (平滑移動平均線)。

2.  **AC2: 進場條件可被準確識別**
    *   系統必須能夠準確識別滿足所有進場條件的訊號，包括：
        *   SMMA 多頭排列 (`smma10 > smma20 > smma50 > smma233`)。
        *   收盤價高於 `smma10` 和 `smma233`。
        *   (可選) 成交量大於前一根 K 棒的 `volumeMultiplier` 倍。
    *   當條件滿足且當前無倉位時，系統應產生一個「開多」訊號。

3.  **AC3: 倉位大小可根據風險計算**
    *   系統必須根據 `risk_percent` (風險百分比) 參數，以及進場價與止損價 (`smma233`) 的距離，來計算正確的開倉單位。

4.  **AC4: 出場條件可被執行**
    *   系統必須能夠設定一個以 `smma233` 為準的動態止損點。
    *   系統必須能夠設定一個動態止盈點 (進場價 + 20 * 風險距離)。
    *   當價格觸及止損或止盈點時，系統應產生「平倉」訊號。

5.  **AC5: 策略可被主協調器呼叫**
    *   新的鱷魚策略必須能夠被 `main.py` 中的主協調器（在 Story 3 中建立）呼叫，並接收強勢標的列表作為輸入。

## 開發任務 (Tasks / Subtasks)

-   **Task 1: 建立策略檔案與類別 (AC1, AC5)**
    -   [x] 1.1 在 `strategy/` 目錄下建立一個新檔案 `alligator_strategy.py`。 [Source: brownfield-architecture.md#程式碼組織與標準]
    -   [x] 1.2 在新檔案中，建立一個名為 `AlligatorStrategy` 的類別，繼承自 `strategy/base.py` 中的 `Strategy` 基礎類別。
    -   [x] 1.3 確保該類別可以接收一個標的名稱和 K 線數據作為輸入。

-   **Task 2: 實作 SMMA 指標計算 (AC1)**
    -   [x] 2.1 在 `AlligatorStrategy` 類別中，實作一個方法來計算 10、20、50、233 週期的 SMMA。
    -   [x] 2.2 SMMA 計算公式為: `(previous_smma * (period - 1) + current_close) / period`。可以考慮使用 `pandas` 來進行高效計算。
    -   [x] 2.3 撰寫單元測試，驗證 SMMA 計算的準確性。 [Source: brownfield-architecture.md#測試整合策略]

-   **Task 3: 實作進場與出場邏輯 (AC2, AC4)**
    -   [x] 3.1 實作一個方法來檢查 SMMA 是否處於多頭排列。
    -   [x] 3.2 實作可選的「突兀量」檢查邏輯。
    -   [x] 3.3 整合所有進場條件，用於判斷是否產生「開多」訊號。
    -   [x] 3.4 實作止損 (`smma233`) 和止盈 (20R) 價格的計算邏輯。

-   **Task 4: 實作倉位大小計算 (AC3)**
    -   [x] 4.1 實作一個方法，根據進場價、止損價和 `risk_percent` 參數來計算倉位大小。
    -   [x] 4.2 確保 `risk_percent` 可以從外部設定檔載入。 [Source: brownfield-architecture.md#設定管理]

-   **Task 5: 整合到主協調器 (AC2, AC3, AC4)**
    -   [x] 5.1 修改 `main.py`，在啟動時初始化 `PositionManager`。
    -   [x] 5.2 當 `AlligatorStrategy` 產生訊號時，呼叫 `position_manager.open_position()`。
    -   [x] 5.3 在主協調器的迴圈中，定期呼叫 `position_manager.update_positions()` 來更新所有倉位的狀態和 PnL。

## 開發者備註 (Dev Notes)

*   **前置故事依賴**: 這個故事的實作，強烈依賴 **Story 2 (現有策略模組化)** 和 **Story 3 (交易訊號產生與通知)** 的完成。請確保 `Strategy` 基礎類別和主協調器 `main.py` 都已準備就緒。

*   **策略邏輯來源**: 本次實作的交易邏輯完全基於您提供的 `tradingview` Pine Script 腳本 (`鱷魚策略_改`)。所有核心計算（SMMA、進出場條件、倉位大小）都必須忠實地還原該腳本的邏輯。

*   **架構遵循**:
    *   **檔案位置**: 新的策略檔案應建立於 `strategy/alligator_strategy.py`。 [Source: brownfield-architecture.md#程式碼組織與標準]
    *   **命名慣例**: 請遵循 `PascalCase` 用於類別命名 (`AlligatorStrategy`)，`snake_case` 用於函式和變數命名。 [Source: brownfield-architecture.md#命名慣例]
    *   **設定管理**: `volumeMultiplier`, `useVolumeCondition`, `risk_percent` 等參數應被視為可設定項，並從 `config.py` 或 `.env` 檔案中讀取，而非寫死。 [Source: brownfield-architecture.md#設定管理]

*   **SMMA 計算**: Pine Script 中的 `smma` 是一種特殊的平滑移動平均線。Pandas 的 `ewm` 函式（指數加權移動平均）在設定 `alpha = 1/period` 時，其行為與 SMMA 非常相似。建議使用 `pandas.DataFrame.ewm(alpha=1/period, adjust=False).mean()` 來進行計算，這比手動迴圈更高效。

*   **測試**: 根據專案的測試策略，請為 `AlligatorStrategy` 的核心邏輯（特別是訊號產生和倉位計算）撰寫 `pytest` 單元測試。 [Source: brownfield-architecture.md#測試整合策略]

*   **上文下理回顧 (來自 Story 3)**: Story 3 建立了 `main.py` 作為主協調器，它會定時執行並獲取強勢標的列表。本故事的 `AlligatorStrategy` 將被這個協調器呼叫，並對這些強勢標的進行分析。請確保兩個故事之間的數據傳遞是順暢的。

## File List

**Created**:
*   `docs/stories/story-4-alligator-strategy-implementation.md`
*   `strategy/alligator_strategy.py`
*   `tests/test_alligator_strategy.py`

**Modified**:
*   `main.py`
*   `config.py` (或 `.env`)


## QA Results

**Reviewer:** Quinn (Test Architect & Quality Advisor)
**Date:** 2025-09-08
**Status:** 🟢 PASS with CONCERNS

### Summary:
The implementation of the Alligator Strategy is functionally correct and meets all acceptance criteria. The code is well-structured, and the unit tests provide good coverage for the core logic. The integration into the main orchestrator was successful.

However, two primary concerns have been identified that should be addressed in the future to improve the system's robustness and maintainability.

### Key Findings:

| Category | Status | Comments |
|---|---|---|
| **Requirements Traceability** | ✅ PASS | All Acceptance Criteria are verifiably met by the implementation and covered by tests. |
| **Code Quality** | ⚠️ CONCERNS | The core strategy code is clean. However, a pre-existing issue was identified where the `StrongTargetScreener` is tightly coupled with the `RelativeStrengthStrategy`, making it inflexible. This is a source of technical debt. |
| **Testing** | ✅ PASS | Unit tests for the new strategy are well-written and cover key logic paths. The integration was validated via a successful smoke test. |
| **Risk Assessment** | ⚠️ CONCERNS | The system is highly susceptible to API rate-limiting errors from Binance, as seen in the smoke tests. The current mitigation (adding delays) is a temporary workaround. A more robust solution (e.g., WebSockets) is recommended for long-term stability. |

### Recommendations:
1.  **HIGH PRIORITY**: Create a new technical story to refactor the data fetching mechanism to use WebSockets for real-time data, as suggested by the Binance API error messages. This will resolve the rate-limiting issue.
2.  **MEDIUM PRIORITY**: Create a technical story to refactor `StrongTargetScreener` to decouple it from any specific strategy, making the screening process more modular.