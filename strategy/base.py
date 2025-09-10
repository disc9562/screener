from abc import ABC, abstractmethod
import pandas as pd
from data.fetcher import CryptoFetcher
from data.transformer import transform_crypto_data

class Strategy(ABC):
    def __init__(self, config):
        self.config = config
        self.crypto_fetcher = CryptoFetcher()

    def run(self, symbol: str, timeframe: str = "1d"):
        """Orchestrates the data fetching, analysis, and signal generation."""
        print(f"Running strategy for {symbol} on {timeframe} timeframe...")
        
        # 1. Fetch Data
        raw_data = self._fetch_data(symbol, timeframe)
        if raw_data is None:
            print(f"Failed to fetch data for {symbol}.")
            return None

        # 2. Transform Data
        df = self._transform_data(raw_data, symbol, timeframe)
        if df.empty:
            print(f"No data after transformation for {symbol}.")
            return None

        # 3. Analyze Data and Generate Signals
        signals = self._analyze(df)
        
        print(f"Strategy for {symbol} completed. Signals: {signals}")
        return signals

    @abstractmethod
    def _fetch_data(self, symbol: str, timeframe: str):
        """Abstract method to fetch raw data based on symbol and timeframe."""
        pass

    @abstractmethod
    def _transform_data(self, raw_data, symbol: str, timeframe: str):
        """Abstract method to transform raw data into a DataFrame."""
        pass

    @abstractmethod
    def _analyze(self, df: pd.DataFrame):
        """Abstract method to analyze the DataFrame and generate signals."""
        pass

    # Helper methods for common fetching/transforming logic
    def _fetch_crypto_data(self, symbol: str, interval: str = "15m", limit: int = 1500):
        return self.crypto_fetcher.fetch_klines(symbol, interval, limit)

    def _transform_crypto_data_default(self, raw_klines) -> pd.DataFrame:
        return transform_crypto_data(raw_klines)
