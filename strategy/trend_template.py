import pandas as pd
import numpy as np
import talib
from strategy.base import Strategy
from data.fetcher import CryptoFetcher
from data.transformer import transform_crypto_data
from config import CRYPTO_SMA_PERIODS, CURRENT_TIMEZONE

class TrendTemplateStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)
        self.crypto_sma_periods = config.get('CRYPTO_SMA_PERIODS')

    def _fetch_data(self, symbol: str, timeframe: str):
        # Crypto data from Binance
        # ttt.py uses limit=280 for 1h, 1500 for 15m
        # We need enough data for 200-period EMA
        limit = 280 if timeframe == "1h" else 1500 # Adjust limit based on timeframe
        return self._fetch_crypto_data(symbol, timeframe, limit=limit)

    def _transform_data(self, raw_data, symbol: str, timeframe: str):
        return self._transform_crypto_data_default(raw_data, CURRENT_TIMEZONE)

    def _analyze(self, df):
        if df.empty:
            return {"signal": "NO_DATA"}

        # Determine SMA periods based on symbol type
        sma_periods = self.crypto_sma_periods

        # Calculate SMAs if not already present (transformer should do this)
        # This is a fallback/double check, as transformer should provide SMAs
        for period in sma_periods:
            if f'SMA_{period}' not in df.columns:
                df[f'SMA_{period}'] = talib.SMA(df['Close'], timeperiod=period)

        # Ensure we have enough data for all SMAs
        if df.isnull().any().any(): # Check for any NaN values after SMA calculation
            df = df.dropna() # Drop rows with NaN values
            if df.empty:
                return {"signal": "INSUFFICIENT_DATA_FOR_SMAS"}

        current_close = df['Close'].iloc[-1]
        
        # Get the latest SMA values
        sma_values = {f'SMA_{p}': df[f'SMA_{p}'].iloc[-1] for p in sma_periods}

        # --- Trend Template Conditions (from ttt.py) ---
        # Condition 1: EMA20 > EMA50 and EMA50 > EMA200
        condition_ma_relationship = False
        if 'SMA_20' in sma_values and 'SMA_50' in sma_values and 'SMA_200' in sma_values:
            if sma_values['SMA_20'] > sma_values['SMA_50'] and sma_values['SMA_50'] > sma_values['SMA_200']:
                condition_ma_relationship = True

        # --- Output Signal ---
        if condition_ma_relationship:
            return {"signal": "UPTREND_CONFIRMED"}
        else:
            return {"signal": "NO_UPTREND"}

    # Helper function from ttt.py (slope) - can be a static method or utility
    @staticmethod
    def slope(point0, point1, point2):
        gap = point2 - point0
        if gap == 0: return 0 # Avoid division by zero
        normalization_point = (point1 - point0) / gap
        normalization_slope = (1 - normalization_point) / 0.5
        return normalization_slope