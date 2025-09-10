# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-1
# Title: 核心架構重構
# Status: Done
# Points: 8 (預估值)
# Priority: Highest
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story)

> 作為一個**系統維護者**，
> 我想要**將共享的邏輯（如數據下載、設定管理）重構成獨立的、可重複使用的模組**，
> 以便**簡化未來新策略的開發與維護，並提高系統的穩定性**。

## 驗收標準 (Acceptance Criteria)

1.  **AC1: 新的專案結構已建立**:
    *   專案根目錄下必須建立 `data/`, `services/`, `strategy/`, `tests/` 這四個新的資料夾。

2.  **AC2: 統一的設定管理已實作**:
    *   必須建立一個 `config.py` 檔案，用於集中管理所有非敏感的應用程式設定（例如，預設的時間框架、檔案路徑等）。
    *   必須實作一個讀取 `.env` 檔案的機制，用於管理所有敏感金鑰（API Keys, Webhook URL）。程式中不得再出現寫死的金鑰。

3.  **AC3: 下載器已被重構為獨立模組**:
    *   所有從外部 API 下載數據的邏輯，都必須從舊的策略腳本中移除，並整合進 `data/` 目錄下的一個或多個新模組中。
    *   新的下載器模組必須能夠被其他模組輕易地引用並使用。

4.  **AC4: 舊腳本已被標記為棄用**:
    *   原有的 `ttt.py`, `crypto_relative_strength.py`, `stock_screener.py` 應被清空或標記為「已棄用」，以防止被意外執行。其功能將在下一個故事中被遷移。

5.  **AC5: 基礎功能可被驗證**:
    *   必須提供一個簡單的測試腳本或筆記本 (`test_refactor.py`)，它能夠成功引用新的設定檔和新的下載器模組，並成功下載一筆數據，以證明重構後的核心功能是可運作的。

## 開發任務 (Tasks / Subtasks)

-   **Task 1: 建立新的專案結構**
    -   [x] 1.1 在專案根目錄建立 `data/`, `services/`, `strategy/`, `tests/` 四個資料夾。
    -   [x] 1.2 在上述新資料夾中都建立 `__init__.py` 檔案，使其成為可被引用的 Python 套件。
    -   [x] 1.3 在根目錄建立一個空白的 `main.py`，作為未來應用程式的統一入口點。

-   **Task 2: 實作設定管理系統**
    -   [x] 2.1 建立 `config.py` 檔案，用於存放非敏感的應用程式設定。
    -   [x] 2.2 建立 `.env.example` 檔案，作為範本，列出所有需要的環境變數（如 `BINANCE_API_KEY`, `DISCORD_WEBHOOK_URL` 等）。
    -   [x] 2.3 將 `python-dotenv` 加入到 `requirements.txt` 中。
    -   [x] 2.4 實作讀取 `.env` 檔案的邏輯，並讓應用程式能從中載入金鑰。

-   **Task 3: 重構下載器模組**
    -   [x] 3.1 在 `data/` 中建立 `fetcher.py`，將所有原始的 API 請求邏輯從舊檔案中搬移至此。
    -   [x] 3.2 在 `fetcher.py` 中，實作 API 錯誤處理與請求頻率限制 (Rate Limit) 的機制。
    -   [x] 3.3 在 `data/` 中建立 `transformer.py`，將所有 Pandas DataFrame 數據轉換的邏輯搬移至此。

-   **Task 4: 加入測試與效能基準**
    -   [x] 4.1 將 `pytest` 和 `pytest-mock` 加入到 `requirements.txt` 中。
    -   [x] 4.2 在 `tests/` 中建立一個簡單的效能測試腳本，用於測量並記錄**舊的** `crypto_relative_strength.py` 腳本的執行時間，作為我們未來優化的基準。
    -   [x] 4.3 在 `tests/` 中為新的 `fetcher.py` 建立單元測試，需使用 Mock 來模擬 API 的回應，以確保測試的獨立性。

-   **Task 5: 完成與文件更新**
    -   [x] 5.1 建立 `test_refactor.py` 腳本，用於展示如何引用新模組並成功下載數據，以滿足 AC5。
    -   [x] 5.2 更新 `README.md`，說明新的專案結構以及未來如何透過 `main.py` 來執行應用程式。
    -   [x] 5.3 在 `README.md` 中新增一個章節，定義一個簡單的「回滾策略」（例如：如何使用 `git revert` 命令）。
    -   [x] 5.4 將舊的策略腳本 (`ttt.py` 等) 的內容清空，或在檔案開頭加入已棄用的警告訊息。

## 開發者備註 (Dev Notes)

*   **關於此故事的重點**:
    *   這個故事的**唯一目標**是進行基礎設施的重構，建立一個乾淨、穩定且可擴展的架構。
    *   **請勿**在此故事中實作任何新的策略邏輯。現有策略的遷移將在下一個故事中進行。我們的目標是先打造一個強壯的地基。

*   **關於測試**:
    *   先前在 PO 的審核中，已將「缺乏測試」視為本專案最大的風險。因此，Task 4 中規劃的**效能基準測試**與**單元測試**至關重要，它們是驗證本次重構成敗的關鍵，請務必確實執行。

*   **關於設定**:
    *   在重構過程中，請留意舊腳本中的任何「神奇數字」（Magic Numbers，即寫死的數值參數）或設定字串，並將它們統一遷移到新的 `config.py` 設定檔中。

*   **關於回滾策略**:
    *   我們在 `README.md` 中定義的回滾策略是一個簡單的手動流程。對於專案的現階段來說，這已經足夠。

## File List

**Created**:
*   `data/`
*   `services/`
*   `strategy/`
*   `tests/`
*   `main.py`
*   `config.py`
*   `.env.example`
*   `data/fetcher.py`
*   `data/transformer.py`
*   `tests/test_performance.py`
*   `tests/test_fetcher.py`
*   `test_refactor.py`

**Modified**:
*   `requirements.txt`
*   `README.md`
*   `crypto_relative_strength.py` (deprecated)
*   `stock_screener.py` (deprecated)
*   `ttt.py` (deprecated)

## QA Results

### Review Date: 2025年9月4日

### Reviewed By: Quinn (Test Architect)

### Code Quality Assessment
整體實作符合模組化重構的目標。新的 `fetcher` 和 `transformer` 模組清晰地分離了職責。程式碼風格良好，易於閱讀。

### Refactoring Performed
在此次審核中，我沒有直接執行任何程式碼重構。

### Compliance Check
- Coding Standards: ✅ (符合 PEP 8 和專案新定義的結構)
- Project Structure: ✅ (完全符合新的專案結構定義)
- Testing Strategy: ⚠️ (部分符合。單元測試已建立，但整合測試和全面覆蓋率仍有待加強)
- All ACs Met: ✅ (所有驗收標準均已達成)

### Improvements Checklist
- [ ] 為 `data/transformer.py` 模組增加單元測試。
- [ ] 增加更全面的整合測試，驗證 `fetcher` 和 `transformer` 模組在實際數據流中的協同工作。
- [ ] 考慮為 `test_refactor.py` 增加斷言，使其成為一個更嚴格的驗證腳本。
- [ ] 強化 `fetcher.py` 中的錯誤處理機制，使其能更優雅地處理 API 錯誤和網路問題。
- [ ] 規劃並實作使用者友善的錯誤訊息。
- [ ] 建立一個持續的效能監控機制，並設定效能目標。
- [ ] 強化 `fetcher.py` 中的頻率限制處理，確保其能適應不同交易所的複雜規則。
- [ ] 在專案中設定一個 Linter (例如 `flake8` 或 `black`)，並將其納入開發流程。

### Security Review
API 金鑰已透過 `.env` 檔案管理，符合基本安全實踐。

### Performance Considerations
已建立舊腳本的效能基準測試，但尚未設定新系統的效能目標或進行優化。

### Files Modified During Review
無。

### Gate Status
Gate: CONCERNS → qa/gates/screener.story-1-core-refactoring.yml
Risk profile: qa/assessments/screener.story-1-core-refactoring-risk-20250904.md
NFR assessment: qa/assessments/screener.story-1-core-refactoring-nfr-20250904.md

### Recommended Status
✓ Ready for Done