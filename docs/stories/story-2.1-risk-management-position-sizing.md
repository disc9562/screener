# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-2.1
# Title: 風險管理與倉位大小計算
# Status: Ready for Review
# Points: 待估計
# Priority: Medium
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story):
作為一個**交易員**，
我想要**自動計算倉位大小，並準確地將手續費納入盈虧計算**，
以便**我能確保一致的風險管理和精確的每筆交易盈虧追蹤**。

## 驗收標準 (Acceptance Criteria):

1.  **AC1: 配置參數**
    *   系統應從 `.env` 檔案中讀取 `TOTAL_CAPITAL` (總倉位，預設 10000 美金), `RISK_PER_TRADE_PERCENT` (每次開倉損失佔總倉位百分比，預設 1%), `TRANSACTION_FEE_PERCENT` (手續費百分比，預設 0.06%)。
2.  **AC2: 倉位大小計算**
    *   `PositionManager` 在開倉時，應根據 `TOTAL_CAPITAL`、`RISK_PER_TRADE_PERCENT`、`entry_price` 和 `stop_loss` 計算 `units` (倉位大小)。
    *   計算公式為：`units = (TOTAL_CAPITAL * RISK_PER_TRADE_PERCENT) / abs(entry_price - stop_loss)`。
    *   如果 `entry_price` 和 `stop_loss` 相同，或計算出的 `units` 為零或負數，應有適當的錯誤處理或日誌記錄。
3.  **AC3: 手續費計算**
    *   `PositionManager` 應在開倉和平倉時，將 `TRANSACTION_FEE_PERCENT` 納入盈虧 (PnL) 計算。
    *   手續費應從實際交易金額中扣除。

## 開發者備註 (Dev Notes):

*   **Previous Story Insights**: Story 7 實作了交易事件和強勢標的列表變動的 Discord 通知。`NotificationService` 已與 `PositionManager` 整合。
*   **Data Models**: `PositionManager` 使用 Pandas DataFrame 儲存倉位數據。`units` 欄位將會被更新。
*   **File Locations**:
    *   `config.py`: 需要更新以載入新的環境變數。
    *   `services/position_manager.py`: `open_position` 方法需要修改以計算 `units`。`update_positions` 方法需要修改以計算包含手續費的 PnL。
    *   `.env.example`: 需要更新以包含新的環境變數範例。
*   **Testing Requirements**: 
    *   `tests/test_position_manager.py` 中的單元測試應更新/新增，以驗證 `units` 的正確計算 (AC2)。
    *   `tests/test_position_manager.py` 中的單元測試應更新/新增，以驗證包含手續費的 PnL 計算 (AC3)。
    *   考慮 `units` 計算的邊界情況 (例如 `entry_price == stop_loss`)。

## 開發任務 (Tasks / Subtasks):

*   [x] **Task 1: 更新配置 (AC: 1)**
    *   [x] 1.1 修改 `config.py`，新增從 `.env` 讀取 `TOTAL_CAPITAL`、`RISK_PER_TRADE_PERCENT` 和 `TRANSACTION_FEE_PERCENT` 的邏輯。
    *   [x] 1.2 更新 `.env.example` 檔案，加入這些新的環境變數範例。
*   [x] **Task 2: 實作倉位大小計算 (AC: 2)**
    *   [x] 2.1 修改 `services/position_manager.py` 中的 `open_position` 方法。
    *   [x] 2.2 在 `open_position` 方法中，根據 `TOTAL_CAPITAL`、`RISK_PER_TRADE_PERCENT`、`entry_price` 和 `stop_loss` 計算 `units`。
    *   [x] 2.3 處理 `entry_price` 等於 `stop_loss` 或計算結果無效的邊界情況。
*   [x] **Task 3: 實作手續費計算 (AC: 3)**
    *   [x] 3.1 修改 `services/position_manager.py` 中的 `open_position` 方法，在計算 `pnl` 時考慮開倉手續費。
    *   [x] 3.2 修改 `services/position_manager.py` 中的 `update_positions` 方法，在計算 `pnl` 時考慮平倉手續費。
*   [x] **Task 4: 撰寫單元測試 (AC: 2, 3)**
    *   [x] 4.1 修改 `tests/test_position_manager.py`，新增或更新測試案例以驗證 `units` 的正確計算。
    *   [x] 4.2 修改 `tests/test_position_manager.py`，新增或更新測試案例以驗證包含手續費的 PnL 計算。

## File List

**Modified**:
*   `config.py`
*   `services/position_manager.py`
*   `tests/test_position_manager.py`
*   `.env.example`

## Dev Agent Record
### Agent Model Used
Gemini (Current Model)
### Debug Log References
- Debug prints were used in `services/position_manager.py` to trace `TRANSACTION_FEE_PERCENT`, `position['units']`, and `floating_pnl_from_price_change` during test failures. These were removed after debugging.
### Completion Notes List
- Implemented `TOTAL_CAPITAL`, `RISK_PER_TRADE_PERCENT`, `TRANSACTION_FEE_PERCENT` loading from `.env` into `config.py`.
- Modified `services/position_manager.py` to calculate `units` based on risk management rules and handle edge cases (zero price difference, non-positive units).
- Incorporated opening and closing transaction fees into PnL calculations in `services/position_manager.py`.
- Updated `tests/test_position_manager.py` with new and corrected test cases for `units` calculation and PnL with fees.
- Fixed `KeyError` in `tests/test_alligator_strategy.py` by correcting column names (`Close`, `Volume`).
- Removed obsolete `tests/test_strategy_migration.py` file.
### File List
**Modified**:
*   `config.py`
*   `services/position_manager.py`
*   `tests/test_position_manager.py`
*   `.env.example`

## Change Log
| Date       | Version | Description                               | Author |
| ---------- | ------- | ----------------------------------------- | ------ |
| 2025-09-09 | 1.0     | Initial implementation of risk management and position sizing. | James  |

## QA Results

### Review Date: 2025-09-09

### Reviewed By: Quinn (Test Architect)

### Code Quality Assessment

Overall, the implementation is clean, adheres to existing patterns, and is well-tested. The logic for calculating units and incorporating fees is clear and correctly implemented.

### Refactoring Performed

None performed by QA.

### Compliance Check

- Coding Standards: ✓
- Project Structure: ✓
- Testing Strategy: ✓
- All ACs Met: ✓

### Improvements Checklist

- [x] All applicable items from the DoD checklist were addressed by the developer.

### Security Review

No new security concerns identified within the scope of this story.

### Performance Considerations

The new calculations are lightweight and are not expected to introduce any performance bottlenecks.

### Files Modified During Review

None.

### Gate Status

Gate: PASS → docs/qa/gates/screener.story-2.1-risk-management-position-sizing.yml
Risk profile: N/A (not generated for this review)
NFR assessment: N/A (not generated for this review)

### Recommended Status

✓ Ready for Done


## Dev Agent Record
### Agent Model Used
Gemini (Current Model)
### Debug Log References
- Debug prints were used in `services/position_manager.py` to trace `TRANSACTION_FEE_PERCENT`, `position['units']`, and `floating_pnl_from_price_change` during test failures. These were removed after debugging.
### Completion Notes List
- Implemented `TOTAL_CAPITAL`, `RISK_PER_TRADE_PERCENT`, `TRANSACTION_FEE_PERCENT` loading from `.env` into `config.py`.
- Modified `services/position_manager.py` to calculate `units` based on risk management rules and handle edge cases (zero price difference, non-positive units).
- Incorporated opening and closing transaction fees into PnL calculations in `services/position_manager.py`.
- Updated `tests/test_position_manager.py` with new and corrected test cases for `units` calculation and PnL with fees.
- Fixed `KeyError` in `tests/test_alligator_strategy.py` by correcting column names (`Close`, `Volume`).
- Removed obsolete `tests/test_strategy_migration.py` file.
### File List
**Modified**:
*   `config.py`
*   `services/position_manager.py`
*   `tests/test_position_manager.py`
*   `.env.example`

## Change Log
| Date       | Version | Description                               | Author |
| ---------- | ------- | ----------------------------------------- | ------ |
| 2025-09-09 | 1.0     | Initial implementation of risk management and position sizing. | James  |