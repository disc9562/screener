import pandas as pd
import numpy as np
import concurrent.futures
import time
from strategy.base import Strategy
from strategy.relative_strength import RelativeStrengthStrategy
from data.fetcher import CryptoFetcher

class StrongTargetScreener(Strategy):
    def __init__(self, config):
        super().__init__(config)
        self.crypto_fetcher = CryptoFetcher() # Re-initialize for symbol fetching
        self.relative_strength_strategy = RelativeStrengthStrategy(config)

    def _fetch_data(self, symbol: str, timeframe: str):
        # This strategy doesn't fetch data for a single symbol in its _fetch_data
        # It orchestrates fetching for many symbols.
        # This method will not be used in the same way as other strategies.
        # It will fetch all symbols first.
        pass

    def _transform_data(self, raw_data, symbol: str, timeframe: str) -> pd.DataFrame:
        # This strategy doesn't transform data for a single symbol.
        pass

    def _analyze(self, df: pd.DataFrame):
        # This strategy doesn't analyze a single DataFrame.
        # It orchestrates the analysis of many symbols.
        pass

    def run_screener(self):
        """
        Orchestrates the process of finding strong targets across all markets.
        """
        print("Running Strong Target Screener...")
        all_strong_targets = []

        # 1. Get all crypto symbols
        # Check for local test mode (Story 2.4)
        if self.config.get('local_test_mode'):
            crypto_symbols = self.config.get('TEST_COIN_SUBSET', [])
            print(f"Running in local test mode. Using subset of {len(crypto_symbols)} crypto symbols: {crypto_symbols}")
        else:
            crypto_symbols = self.crypto_fetcher.get_all_symbols()
            print(f"Found {len(crypto_symbols)} crypto symbols.")

        # 2. Run RelativeStrengthStrategy on all crypto symbols
        print("Running RelativeStrengthStrategy on crypto...")
        crypto_strong_targets = self._run_strategy_on_symbols(
            self.relative_strength_strategy, crypto_symbols, "15m"
        )
        all_strong_targets.extend(crypto_strong_targets)
        print(f"Found {len(crypto_strong_targets)} strong cryptos.")

        print(f"Total strong targets found: {len(all_strong_targets)}")
        return all_strong_targets

    def _run_strategy_on_symbols(self, strategy_instance, symbols, timeframe):
        """Helper to run a strategy on a list of symbols using ThreadPoolExecutor."""
        strong_targets = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.get('MAX_WORKERS', 2)) as executor:
            future_to_symbol = {}
            for symbol in symbols:
                future = executor.submit(strategy_instance.run, symbol, timeframe)
                future_to_symbol[future] = symbol
                time.sleep(0.25)  # 250ms delay to avoid rate limiting
            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    result = future.result()
                    if result and result.get("signal") == "STRONG_RS":
                        strong_targets.append(symbol)
                except Exception as exc:
                    print(f'{symbol} generated an exception: {exc}')
        return strong_targets
