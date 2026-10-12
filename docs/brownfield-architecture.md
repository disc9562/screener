# Screener 專案棕地架構文件

## 1. 簡介

本文件旨在記錄 `Screener` 專案的當前狀態，包含其技術債、實際運作模式與待解決問題。本文件的目標是作為後續 AI 代理人進行**程式碼重構**與**新增交易邏輯**（基於 15m K線均線開倉、倉位管理、計算盈虧）時的共同理解基礎與開發藍圖。

### 1.1. 文件範疇

本文件將聚焦於以下幾個方面：
*   分析現有的四個核心檔案 (`stock_screener.py`, `ttt.py`, `crypto_relative_strength.py`, `src/downloader.py`) 的複雜性。
*   規劃一個可擴展、易於維護的新架構。
*   為即將開發的交易與通知功能提供設計思路。

### 1.2. 變更日誌

| 日期 | 版本 | 描述 | 作者 |
| :--- | :--- | :--- | :--- |
| 2025-09-04 | 1.0 | 初始棕地分析與架構建立 | Winston (Architect) |

## 2. 高層架構

### 2.1. 技術摘要

此專案是一個 Python 應用程式，主要用於從各大交易所（如 Binance, Bybit, OKX, Polygon.io）下載加密貨幣與美股的歷史 K 線數據。它包含數個獨立的策略腳本，用於分析數據、篩選出符合特定條件（如 Mark Minervini 的趨勢範本、相對強度）的強勢標的，並將結果輸出至文字檔，以便匯入 TradingView。

下一個主要目標是基於這些篩選出的標的，進行更細緻的 15 分鐘線圖分析，並在滿足均線條件時，模擬開倉、管理預設倉位並計算盈虧，最後透過 Discord Webhook 發送通知。

### 2.2. 現實世界中的技術堆疊

| 分類 | 技術 | 版本 | 備註 |
| :--- | :--- | :--- | :--- |
| 語言 | Python | 3.x | - |
| 數據分析 | Pandas, NumPy | - | 核心數據處理 |
| 數據源 (Crypto) | python-binance, ccxt | - | 連接幣安、Bybit、OKX |
| 數據源 (Stock) | yfinance, stocksymbol | - | 連接 Yahoo Finance, Polygon.io |
| 技術分析 | TA-Lib | - | 用於計算 EMA 等指標 |
| 通知 | discord-webhook | - | 已包含在依賴中，用於發送 Discord 通知 |
| 排程 | APScheduler, schedule | - | 用於定時執行腳本 |
| API 請求 | requests, tenacity | - | 用於底層 HTTP 請求與重試 |

### 2.3. 儲存庫結構現實查核

*   **類型**: 單體式腳本集合 (Collection of monolithic scripts)。
*   **套件管理**: pip + `requirements.txt`。
*   **顯著特點**: 核心商業邏輯散落在根目錄的多個獨立 Python 腳本中，共享一個位於 `src/` 的下載模組。缺乏統一的應用程式入口點或設定檔。

## 3. 原始碼樹與模組組織

### 3.1. 專案結構 (現狀)

```
screener/
├── src/
│   └── downloader.py      # 負責從各個 API 下載數據的模組
├── crypto_relative_strength.py # 加密貨幣相對強度策略腳本
├── stock_screener.py      # 美股趨勢範本策略腳本 (Mark Minervini)
├── ttt.py                 # 另一個加密貨幣趨勢策略腳本
├── requirements.txt       # 專案依賴
└── README.md              # 專案說明
```

### 3.2. 關鍵模組與其目的

*   **`crypto_relative_strength.py`**: 獨立腳本。計算加密貨幣的相對強度分數，並找出強勢標的。
*   **`stock_screener.py`**: 獨立腳本。套用 Mark Minervini 的趨勢範本篩選美股。
*   **`ttt.py`**: 獨立腳本。使用 EMA 均線策略篩選加密貨幣，並排程執行。
*   **`src/downloader.py`**: 共享模組。被上述腳本引用，封裝了從不同交易所（Binance, Polygon）下載 K 線數據的邏輯。

## 4. 技術債與已知問題

1.  **程式碼冗長與重複**:
    *   **問題**: `stock_screener.py`, `ttt.py`, `crypto_relative_strength.py` 三個檔案中存在大量重複的邏輯，例如：讀取交易對、數據下載、使用 `concurrent.futures` 進行並行處理、結果格式化與寫入檔案。
    *   **影響**: 維護困難，修改一個邏輯需要在多個地方同步變更，容易出錯。

2.  **可讀性與命名問題**:
    *   **問題**: 部分參數命名不清晰（如 `ttt.py` 中的 `condition5`），腳本內混合了數據處理、策略計算和結果輸出等多種職責，導致閱讀和理解困難。
    *   **影響**: 新開發者難以上手，未來擴充新策略的成本高。

3.  **複雜的數據轉換**:
    *   **問題**: `src/downloader.py` 中混合了 API 請求和 pandas DataFrame 的格式轉換，邏輯耦合度高。
    *   **影響**: 使 downloader 模組難以測試和擴充以支援新的數據源或格式。

4.  **缺乏統一設定**:
    *   **問題**: 策略參數（如均線週期、時間框架）和 API 金鑰等設定散落在程式碼中（即「神奇數字」），沒有統一的設定檔。
    - **影響**: 調整策略需要直接修改程式碼，風險高且不便。

5.  **缺乏自動化測試**:
    *   **問題**: 專案沒有任何單元測試或整合測試。
    *   **影響**: 重構和新增功能時，無法保證原有邏輯的正確性，穩定性低。

## 5. 針對新需求的影響分析與重構計畫

為了支援「重構」與「新增交易邏輯」的需求，建議將當前的腳本導向一個更模組化、可擴展的架構。

### 5.1. 建議的重構策略

1.  **建立核心 `Strategy` 抽象層**:
    *   建立一個 `strategy/` 目錄。定義一個基礎的 `Strategy` 類別，包含 `fetch_data()`, `analyze()`, `generate_signals()` 等標準方法。
    *   將 `ttt.py`, `crypto_relative_strength.py` 等重構為繼承此基礎類別的具體策略類別，它們只需實作自己獨特的 `analyze` 邏輯。

2.  **重構 `Downloader`**:
    *   將 `src/downloader.py` 拆分為 `data_fetcher.py` 和 `data_transformer.py`。前者只負責與 API 互動並獲取原始數據；後者負責將原始數據轉換為標準化的 pandas DataFrame。

3.  **引入統一設定檔**:
    *   建立一個 `config.py` 或 `config.yaml`，集中管理所有策略參數、API 金鑰、時間框架、Webhook URL 等。

4.  **建立 `Signal` 與 `Notification` 服務**:
    *   定義一個標準的 `Signal` 資料類別（dataclass），包含標的、方向、時間等資訊。
    *   建立 `services/notification_service.py`，專門負責接收 `Signal` 物件並發送到 Discord。

5.  **建立 `Trading` 服務**:
    *   建立 `services/trading_service.py`，用於處理新的交易邏輯。它將接收 `Signal`，管理倉位（即使是模擬的），並計算盈虧。

6.  **建立統一的應用程式入口點**:
    *   建立 `main.py`，作為應用的主入口。它負責讀取設定、初始化策略、執行排程任務，並將策略產生的信號傳遞給交易與通知服務。

### 5.2. 需要修改的檔案

*   `stock_screener.py` (將被重構並整合)
*   `ttt.py` (將被重構並整合)
*   `crypto_relative_strength.py` (將被重構並整合)
*   `src/downloader.py` (將被拆分與重構)

### 5.3. 需要建立的新檔案/模組

*   `main.py` (應用程式主入口)
*   `config.py` (統一設定檔)
*   `strategy/base.py` (策略基礎類別)
*   `strategy/ttt_strategy.py` (TTT 策略實作)
*   `strategy/rs_strategy.py` (相對強度策略實作)
*   `data/data_fetcher.py` (數據獲取模組)
*   `data/data_transformer.py` (數據轉換模組)
*   `services/notification_service.py` (通知服務)
*   `services/trading_service.py` (交易服務)
*   `tests/` (測試目錄，用於存放新的單元測試)