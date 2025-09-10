# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-2.2
# Title: 多策略通知分流與「突兀量」開關
# Status: Ready for Review
# Points: 待估計
# Priority: High
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story):
作為一個**交易員**，
我想要**能夠同時運行開啟和未開啟「突兀量」的策略，並將它們的交易通知分別發送到不同的 Discord Webhook**，
以便**我可以清楚地比較兩種策略的表現，並根據不同的通知管道進行監控**。

## 驗收標準 (Acceptance Criteria):

1.  **AC1: 策略同時運行**
    *   系統應能夠同時運行兩個或更多個策略實例，每個實例可以獨立配置其「突兀量」開關狀態。
    *   策略的核心運算邏輯保持一致，僅開倉時的交易量判斷邏輯可根據配置開關。
2.  **AC2: 多 Webhook 配置**
    *   系統應支援配置兩個獨立的 Discord Webhook URL：`DISCORD_WEBHOOK_URL_VOLUME_ON` (用於開啟突兀量策略的通知) 和 `DISCORD_WEBHOOK_URL_VOLUME_OFF` (用於未開啟突兀量策略的通知)。
    *   這些 URL 應從 `.env` 檔案中讀取。
3.  **AC3: 通知路由**
    *   `NotificationService` 應根據傳入的布林值（指示是否使用突兀量）將交易通知路由到對應的 Discord Webhook。
4.  **AC4: 通知內容標識**
    *   交易通知內容應清晰標識該通知是來自「開啟突兀量」的策略還是「未開啟突兀量」的策略。
5.  **AC5: 嚴格的 Webhook 配置驗證**
    *   在程式啟動時，必須驗證 `DISCORD_WEBHOOK_URL_VOLUME_ON` 和 `DISCORD_WEBHOOK_URL_VOLUME_OFF` 是否都已配置。
    *   如果任何一個 Webhook URL 未配置，程式應立即停止運行並發出錯誤訊息。

## 開發者備註 (Dev Notes):

*   **Previous Story Insights**: Story 2.1 實作了風險管理和倉位大小計算。Story 7 實作了 Discord 通知服務。
*   **File Locations**:
    *   `main.py`: 需要修改以實例化和管理多個策略實例和通知服務。
    *   `config.py`: 需要新增兩個 Discord Webhook URL 的配置。
    *   `services/notification_service.py`: 需要修改以支援多個 Webhook URL 和通知路由。
    *   `strategy/alligator_strategy.py`: 需要修改以根據配置開關控制交易量判斷邏輯。
    *   `services/position_manager.py`: 需要修改以將策略類型（例如 `is_volume_on` 布林值）傳遞給 `NotificationService`。
    *   `.env.example`: 需要更新以包含新的環境變數範例。
*   **Strict Validation (AC5)**: 啟動時的驗證邏輯應在 `main.py` 中實現，確保所有必要的 Webhook URL 都已配置。
*   **Strategy Configuration**: `AlligatorStrategy` 的配置中應新增一個布林值（例如 `use_volume_condition`），用於控制是否啟用交易量判斷。
*   **Notification Routing**: `NotificationService` 可以透過接收一個額外參數（例如 `strategy_type` 或 `is_volume_on`）來決定將通知發送到哪個 Webhook。或者，`NotificationService` 可以實例化為兩個獨立的服務，每個服務綁定一個 Webhook URL。

## 開發任務 (Tasks / Subtasks):

*   [x] **Task 1: 更新配置**
    *   [x] 1.1 修改 `config.py`，新增從 `.env` 讀取 `DISCORD_WEBHOOK_URL_VOLUME_ON` 和 `DISCORD_WEBHOOK_URL_VOLUME_OFF` 的邏輯。
    *   [x] 1.2 更新 `.env.example` 檔案，加入這些新的環境變數範例。
*   [x] **Task 2: 實作嚴格的 Webhook 配置驗證**
    *   [x] 2.1 修改 `main.py`，在啟動時驗證兩個 Webhook URL 是否都已配置，否則停止運行。
*   [x] **Task 3: 修改 `NotificationService` 以支援多 Webhook 路由**
    *   [x] 3.1 修改 `NotificationService.__init__` 以接收多個 Webhook URL 或一個包含映射的字典。
    *   [x] 3.2 修改 `NotificationService.send_trade_notification` 以接收一個指示策略類型的參數（例如 `is_volume_on`）。
    *   [x] 3.3 根據 `is_volume_on` 參數將通知路由到正確的 Webhook。
    *   [x] 3.4 在通知內容中添加標識（AC4）。
*   [x] **Task 4: 調整策略以支援「突兀量」開關**
    *   [x] 4.1 修改 `strategy/alligator_strategy.py`，使其 `_analyze` 方法中的交易量判斷邏輯受配置開關控制。
*   [x] **Task 5: 修改 `main.py` 以同時運行多個策略實例**
    *   [x] 5.1 在 `main.py` 中實例化兩個 `AlligatorStrategy` 和 `PositionManager` 實例，一個開啟突兀量，一個關閉。
    *   [x] 5.2 調整主迴圈以處理來自兩個 `PositionManager` 實例的通知。
*   [x] **Task 6: 撰寫單元測試**
    *   [x] 6.1 修改 `tests/test_notification_service.py`，新增或更新測試案例以驗證多 Webhook 路由和通知內容標識。
    *   [x] 6.2 修改 `tests/test_alligator_strategy.py`，新增或更新測試案例以驗證「突兀量」開關的行為。

## File List

**Modified**:
*   `config.py`
*   `services/notification_service.py`
*   `services/position_manager.py`
*   `strategy/alligator_strategy.py`
*   `main.py`
*   `tests/test_notification_service.py`
*   `tests/test_alligator_strategy.py`
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
- Implemented multi-strategy notification routing and "突兀量" switch control.
- Modified `NotificationService` to handle multiple webhooks and route notifications based on strategy type.
- Modified `main.py` to instantiate and manage multiple strategy instances.
- Updated `tests/test_notification_service.py` and `tests/test_alligator_strategy.py` to cover new functionality.
### File List
**Modified**:
*   `config.py`
*   `services/notification_service.py`
*   `services/position_manager.py`
*   `strategy/alligator_strategy.py`
*   `main.py`
*   `tests/test_notification_service.py`
*   `tests/test_alligator_strategy.py`
*   `.env.example`

## Change Log
| Date       | Version | Description                               | Author |
| ---------- | ------- | ----------------------------------------- | ------ |
| 2025-09-09 | 1.0     | Initial implementation of risk management and position sizing. | James  |
| 2025-09-09 | 1.1     | Implemented multi-strategy notification routing and "突兀量" switch control. | James  |

## QA Results

### Review Date: 2025-09-09

### Reviewed By: Quinn (Test Architect)

### Code Quality Assessment

Overall, the implementation is clean, adheres to existing patterns, and is well-tested. The logic for multi-strategy notification routing and the "突兀量" switch control is clear and correctly implemented.

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

Security is improved by the strict validation of Discord webhook URLs at startup.

### Performance Considerations

The changes are not expected to introduce any significant performance bottlenecks.

### Files Modified During Review

None.

### Gate Status

Gate: PASS → docs/qa/gates/screener.story-2.2-multi-strategy-notifications.yml
Risk profile: N/A (not generated for this review)
NFR assessment: N/A (not generated for this review)

### Recommended Status

✓ Ready for Done
