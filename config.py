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

# --- Shotgun v3（docs/shotgun-v3-bot-spec.md）---
# NOTIONAL_PCT = 0.20 搭配 DISASTER_STOP_PCT = 0.20 => 單筆打到嚴格止損時最多損失總權益 4%（隨總權益複利縮放）
NOTIONAL_PCT = float(os.getenv("NOTIONAL_PCT", "0.20"))
DISASTER_STOP_PCT = float(os.getenv("DISASTER_STOP_PCT", "0.20"))
RS_MODE = os.getenv("RS_MODE", "alligator")
MIN_DAILY_QUOTE_VOL = float(os.getenv("MIN_DAILY_QUOTE_VOL", "1000000"))
EXCLUDE_SYMBOLS = [s for s in os.getenv("EXCLUDE_SYMBOLS", "BTCDOMUSDT").split(",") if s]
POSITIONS_CSV_V3 = "data/positions_shotgun_v3.csv"
# 影子記錄（只記錄、不下單）：每日名單＋各時框斜率、每筆進場的斜率
SHADOW_LISTS_CSV = "data/shadow_lists.csv"
SHADOW_ENTRIES_CSV = "data/shadow_entries.csv"
# 代幣化股票／商品／穩定幣（同研究 portfolio_backtest.EQUITY_LIKE + 穩定幣對）
EQUITY_LIKE_BASES = set("""SNDK SKHYNIX SOXL XAU XAG MU KORU DKNG UBER DIS TTWO SONY
AAPL TSLA NVDA MSFT AMZN GOOGL META NFLX AMD INTC COIN MSTR HOOD PLTR SPY QQQ IWM
GLD SLV TQQQ SOXX ARKK BABA NIO PYPL SQ SHOP ABNB RIVN LCID F GM BA JPM GS V MA
WMT COST PEP KO MCD NKE SBUX CRM ORCL ADBE AVGO QCOM TXN ASML TSM ARM SMCI DELL
HPQ IBM CSCO T VZ XOM CVX OXY PFE JNJ UNH LLY MRNA ABBV TMO DHR CAT DE MMM GE HON
UPS FDX LMT RTX NOC USDC FDUSD TUSD USDP DAI USDE BUSD EUR AEUR""".split())

print("Configuration loaded.")
