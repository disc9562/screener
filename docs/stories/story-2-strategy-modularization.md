# Epic: Screener V2: 核心重構與交易訊號自動化
# Story ID: story-2
# Title: 現有策略模組化
# Status: Ready for Review
# Points: 5 (預估值)
# Priority: High
# Owner: Bob (Scrum Master)

## 使用者故事 (User Story)

> 作為一個**策略開發者**，
> 我想要**將現有的三個策略 (`ttt`, `relative_strength`, `stock_screener`) 遷移到新的策略框架中**，
> 以便**驗證新架構的可行性，並能統一地管理所有策略**。

## 驗收標準 (Acceptance Criteria)

1.  **AC1: 策略基礎類別已建立**:
    *   在 `strategy/` 目錄下必須建立一個 `base.py` 檔案，其中定義了一個抽象的 `Strategy` 基礎類別，包含所有策略共用的方法（如 `run`, `_fetch_data`, `_analyze` 等）。

2.  **AC2: 三個現有策略已遷移**:
    *   必須建立三個新的策略類別檔案（例如 `strategy/trend_template.py`, `strategy/relative_strength.py`），分別對應 `ttt`、`crypto_relative_strength` 和 `stock_screener` 的邏輯。
    *   每一個新策略類別都必須繼承自 `Strategy` 基礎類別。

3.  **AC3: 核心邏輯已分離**:
    *   舊腳本中的核心分析演算法，必須被完整地遷移到對應的新策略類別的 `_analyze` 方法中。

4.  **AC4: 策略可被主程式執行**:
    *   `main.py` 必須被更新，使其能夠根據設定檔或命令列參數，來選擇、初始化並執行一個指定的策略。

5.  **AC5: 重構後的結果一致**:
    *   執行任何一個重構後的新策略，其產出的強勢標的列表，必須與執行其對應的舊腳本所產出的結果**完全相同**。

## 開發任務 (Tasks / Subtasks)

-   **Task 1: 建立策略基礎類別**
    -   [x] 1.1 在 `strategy/` 目錄下建立 `base.py` 檔案。
    -   [x] 1.2 在 `base.py` 中，定義一個抽象基礎類別 `Strategy`，並包含一個 `run()` 公用方法以及 `_analyze()` 等待子類別實作的私有抽象方法。

-   **Task 2: 遷移「趨勢範本」策略**
    -   [x] 2.1 在 `strategy/` 目錄下建立 `trend_template.py`。
    -   [x] 2.2 在新檔案中建立一個 `TrendTemplateStrategy` 類別，繼承自 `Strategy`。
    -   [x] 2.3 將 `ttt.py` 和 `stock_screener.py` 中的核心篩選邏輯，整合並實作到 `TrendTemplateStrategy` 的 `_analyze` 方法中。
    -   [x] 2.4 在 `tests/` 目錄下為 `TrendTemplateStrategy` 撰寫單元測試，以驗證其分析邏輯的正確性。

-   **Task 3: 遷移「相對強度」策略**
    -   [x] 3.1 在 `strategy/` 目錄下建立 `relative_strength.py`。
    -   [x] 3.2 在新檔案中建立一個 `RelativeStrengthStrategy` 類別，繼承自 `Strategy`。
    -   [x] 3.3 將 `crypto_relative_strength.py` 中的核心計算邏輯，遷移到 `RelativeStrengthStrategy` 的 `_analyze` 方法中。
    -   [x] 3.4 在 `tests/` 目錄下為 `RelativeStrengthStrategy` 撰寫單元測試。

-   **Task 4: 更新主程式入口點**
    -   [x] 4.1 修改 `main.py`，使其能夠從設定檔或命令列參數中讀取要執行的策略名稱。
    -   [x] 4.2 在 `main.py` 中實作動態載入並執行所選策略類別的 `run()` 方法。

-   **Task 5: 驗證結果一致性**
    -   [x] 5.1 建立一個驗證腳本 `test_strategy_migration.py`。
    -   [x] 5.2 此腳本需要能夠分別執行舊的策略腳本與對應的新策略類別，並比對兩者產出的標的列表檔案。
    -   [x] 5.3 腳本中必須包含斷言 (Assert)，確保新舊版本的產出是完全一致的，以滿足 AC5。

## 開發者備註 (Dev Notes)

*   **重要前置依賴**: 這個故事**完全依賴**故事 1 的完成。請務必在故事 1 的所有任務都完成並通過驗收後，才開始進行此故事的開發。你需要由故事 1 建立的專案架構與模組。

*   **核心目標是「平移」，而非「創新」**: 此故事的重點是將現有邏輯**一模一樣地**搬移到新框架下，並證明其結果與過去完全相同。請勿在此階段試圖優化或修改原有的策略演算法。Task 5 的驗證腳本是此故事成功與否的最終標準。

*   **關於趨勢範本策略的提示**: `ttt.py` 和 `stock_screener.py` 的核心思想似乎都源於「趨勢範本」。在執行 Task 2 時，請評估是否可以將它們整合成一個更通用的、可設定的 `TrendTemplateStrategy` 類別，而不是建立兩個獨立的類別。

*   **測試方法**: 在為策略的分析邏輯撰寫單元測試時，請務必使用 Mock 來提供輸入數據，以確保測試的重點是演算法本身，而不是它所依賴的數據獲取功能。

*   **效能優化目標**: 舊腳本的執行時間較長（例如 `crypto_relative_strength.py`），這是一個已知的效能瓶頸。新的模組化架構為未來的效能優化提供了基礎，但具體的優化工作將在後續的故事中進行。

## File List

**Created**:
*   `strategy/base.py`
*   `strategy/trend_template.py`
*   `strategy/relative_strength.py`
*   `tests/test_strategy_migration.py`

**Modified**:
*   `main.py`
*   `config.py`
*   `data/transformer.py`
*   `strategy/base.py`
*   `strategy/relative_strength.py`
*   `strategy/trend_template.py`
