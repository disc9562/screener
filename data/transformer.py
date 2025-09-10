import pandas as pd
import pytz
import talib
from config import CRYPTO_SMA_PERIODS

def transform_crypto_data(raw_klines, timezone_str="America/Los_Angeles"):
    """Transforms raw kline data from Binance into a DataFrame with SMAs."""
    if not raw_klines:
        return pd.DataFrame()

    df = pd.DataFrame(raw_klines, columns=["Datetime", "Open", "High", "Low", "Close",
                                           "Volume", "Close Time", "Quote Volume", "Number of Trades",
                                           "Taker buy base asset volume", "Taker buy quote asset volume", "Ignore"])

    # Select and convert data types
    df = df[["Datetime", "Open", "High", "Low", "Close", "Volume"]]
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col])

    # Handle timestamp and timezone
    local_timezone = pytz.timezone(timezone_str)
    df["Datetime"] = pd.to_datetime(df['Datetime'], unit='ms', utc=True).dt.tz_convert(local_timezone)

    # Calculate SMAs
    for period in CRYPTO_SMA_PERIODS:
        df[f"SMA_{period}"] = df["Close"].rolling(window=period).mean()
    
    # Drop rows with NaN values from SMA calculation
    df = df.dropna()

    return df.reset_index(drop=True)
