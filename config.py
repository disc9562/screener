import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- API Keys ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

# --- Testnet Configuration ---
USE_TESTNET = os.getenv("USE_TESTNET", "false").lower() == "true"
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
BINANCE_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")

DISCORD_WEBHOOK_URL_VOLUME_ON = os.getenv("DISCORD_WEBHOOK_URL_VOLUME_ON", "")
DISCORD_WEBHOOK_URL_VOLUME_OFF = os.getenv("DISCORD_WEBHOOK_URL_VOLUME_OFF", "")
DISCORD_WEBHOOK_URL_GENERAL_TARGETS = os.getenv("DISCORD_WEBHOOK_URL_GENERAL_TARGETS", "")

# --- Risk Management & Fees ---
TOTAL_CAPITAL = float(os.getenv("TOTAL_CAPITAL", "10000")) # Default to 10000 USD
RISK_PER_TRADE_PERCENT = float(os.getenv("RISK_PER_TRADE_PERCENT", "0.01")) # Default to 1%
TRANSACTION_FEE_PERCENT = float(os.getenv("TRANSACTION_FEE_PERCENT", "0.0006")) # Default to 0.06%

# --- Application Settings ---
# Example: Default timeframe for strategies
DEFAULT_TIMEFRAME = "1d"

# For strong target scheduling (Story 2.3)
TARGET_FETCH_TIMES = os.getenv("TARGET_FETCH_TIMES", "08:00,20:00").split(',')

STOCK_SMA_PERIODS = [20, 30, 45, 50, 60, 150, 200]
CRYPTO_SMA_PERIODS = [30, 45, 60]

RS_CALC_DAYS = 3
RS_TIME_INTERVAL = "15m"
CURRENT_TIMEZONE = "America/Los_Angeles"

MIN_TURNOVER = 10000000
MAX_RETRIES = 3
INITIAL_DELAY = 4  # seconds
MAX_WORKERS = 2

# --- Strategy Specific Settings ---
# For AlligatorStrategy
USE_VOLUME_CONDITION = True
VOLUME_MULTIPLIER = 2.5
EQUITY = 100000
RISK_PERCENT = 0.05


# --- File Paths ---
OUTPUT_DIR = "output"

# For local test mode (Story 2.4)
TEST_COIN_SUBSET = os.getenv("TEST_COIN_SUBSET", "BTCUSDT,ETHUSDT").split(',')

def get_strategy_config():
    """Gathers all strategy-related configurations into a dictionary.

    Note: Testnet API keys and USE_TESTNET are intentionally excluded here.
    They are imported directly as module-level constants where needed (e.g. main.py).
    """
    return {
        "DEFAULT_TIMEFRAME": DEFAULT_TIMEFRAME,
        "STOCK_SMA_PERIODS": STOCK_SMA_PERIODS,
        "CRYPTO_SMA_PERIODS": CRYPTO_SMA_PERIODS,
        "RS_CALC_DAYS": RS_CALC_DAYS,
        "RS_TIME_INTERVAL": RS_TIME_INTERVAL,
        "CURRENT_TIMEZONE": CURRENT_TIMEZONE,
        "MIN_TURNOVER": MIN_TURNOVER,
        "MAX_RETRIES": MAX_RETRIES,
        "INITIAL_DELAY": INITIAL_DELAY,
        "MAX_WORKERS": MAX_WORKERS,
        "use_volume_condition": USE_VOLUME_CONDITION,
        "volume_multiplier": VOLUME_MULTIPLIER,
        "equity": EQUITY,
        "risk_percent": RISK_PERCENT,
        "TOTAL_CAPITAL": TOTAL_CAPITAL,
        "RISK_PER_TRADE_PERCENT": RISK_PER_TRADE_PERCENT,
        "TRANSACTION_FEE_PERCENT": TRANSACTION_FEE_PERCENT,
        "DISCORD_WEBHOOK_URL_VOLUME_ON": DISCORD_WEBHOOK_URL_VOLUME_ON,
        "DISCORD_WEBHOOK_URL_VOLUME_OFF": DISCORD_WEBHOOK_URL_VOLUME_OFF,
        "DISCORD_WEBHOOK_URL_GENERAL_TARGETS": DISCORD_WEBHOOK_URL_GENERAL_TARGETS,
        "TARGET_FETCH_TIMES": TARGET_FETCH_TIMES,
        "TEST_COIN_SUBSET": TEST_COIN_SUBSET,
    }

# --- Shotgun v3（docs/shotgun-v3-bot-spec.md；v3.1 開關與倉位見 docs/shotgun-v3.1-gate.md）---
# 每筆名目 = 下單權益 × NOTIONAL_PCT，最多 MAX_POSITIONS 檔 → 總名目最多 100%。
# 想要更小回撤：NOTIONAL_PCT=0.05、MAX_POSITIONS=20。原本 0.20 且不限檔數，研究中複利三年剩 0.34 倍。
NOTIONAL_PCT = float(os.getenv("NOTIONAL_PCT", "0.10"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
DISASTER_STOP_PCT = float(os.getenv("DISASTER_STOP_PCT", "0.20"))
# 新倉開關：BTC 日線 > EMA200 且 寬度 ≥ BREADTH_ON；任一確定關掉 → 全部平倉
BREADTH_ON = float(os.getenv("BREADTH_ON", "0.5"))
BREADTH_MIN_DAILY_QV = float(os.getenv("BREADTH_MIN_DAILY_QV", "5000000"))
RS_MODE = os.getenv("RS_MODE", "alligator")
MIN_DAILY_QUOTE_VOL = float(os.getenv("MIN_DAILY_QUOTE_VOL", "1000000"))
EXCLUDE_SYMBOLS = [s for s in os.getenv("EXCLUDE_SYMBOLS", "BTCDOMUSDT").split(",") if s]
POSITIONS_CSV_V3 = "data/positions_shotgun_v3.csv"
# 影子記錄（只記錄、不下單）：每日名單＋各時框斜率、每筆進場的斜率
SHADOW_LISTS_CSV = "data/shadow_lists.csv"
SHADOW_ENTRIES_CSV = "data/shadow_entries.csv"
# 實盤母體只取 Binance 標記為 COIN 的永續（股票／商品是 TRADIFI_PERPETUAL、指數是 INDEX，不會進來），
# 再排除這些非加密資產：穩定幣與黃金代幣（同使用者研究 download_hist.py 的母體）
NON_CRYPTO_BASES = set("USDC FDUSD TUSD USDP DAI USDE USD1 BUSD EUR AEUR XAUT PAXG".split())
# 代幣化股票／商品／穩定幣（同研究 portfolio_backtest.EQUITY_LIKE + 穩定幣對）。只留給研究工具；
# 實盤不再用：股票代號和 CVX（Convex）、T（Threshold）、F（SynFutures）、CAT（1000CAT）等幣撞名，會誤排除
EQUITY_LIKE_BASES = set("""SNDK SKHYNIX SOXL XAU XAG MU KORU DKNG UBER DIS TTWO SONY
AAPL TSLA NVDA MSFT AMZN GOOGL META NFLX AMD INTC COIN MSTR HOOD PLTR SPY QQQ IWM
GLD SLV TQQQ SOXX ARKK BABA NIO PYPL SQ SHOP ABNB RIVN LCID F GM BA JPM GS V MA
WMT COST PEP KO MCD NKE SBUX CRM ORCL ADBE AVGO QCOM TXN ASML TSM ARM SMCI DELL
HPQ IBM CSCO T VZ XOM CVX OXY PFE JNJ UNH LLY MRNA ABBV TMO DHR CAT DE MMM GE HON
UPS FDX LMT RTX NOC USDC FDUSD TUSD USDP DAI USDE BUSD EUR AEUR""".split())

print("Configuration loaded.")
