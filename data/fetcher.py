import time
import json
from pathlib import Path
from binance import Client
from binance.exceptions import BinanceAPIException
import requests # New import for handling connection errors
from polygon import RESTClient
from urllib3.util.retry import Retry
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class BaseFetcher:
    """Base class for all data fetchers."""
    def __init__(self):
        pass

    def fetch(self, *args, **kwargs):
        raise NotImplementedError("Fetch method must be implemented by subclasses.")

class CryptoFetcher(BaseFetcher):
    """Fetches crypto data from Binance."""
    def __init__(self):
        super().__init__()
        try:
            self.client = Client(requests_params={"timeout": 30})
        except Exception as e:
            print(f"Error initializing CryptoFetcher: {e}")
            self.client = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception_type((BinanceAPIException, requests.exceptions.ConnectionError, requests.exceptions.Timeout)))
    def fetch_klines(self, symbol, interval="15m", limit=1500):
        if not self.client:
            return None

        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            return klines
        except BinanceAPIException as e:
            if e.code == -1003: # Too many requests
                print(f"Rate limit hit for {symbol}. Retrying...")
                raise # Re-raise to trigger tenacity retry
            else:
                print(f"Error fetching klines for {symbol}: {e}")
                return None
        except Exception as e:
            print(f"Error fetching klines for {symbol}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception_type((BinanceAPIException, requests.exceptions.ConnectionError, requests.exceptions.Timeout)))
    def get_all_symbols(self):
        """Get all USDT pairs from Binance."""
        if not self.client:
            return []
        
        try:
            binance_response = self.client.futures_exchange_info()
            binance_symbols = set()
            for item in binance_response["symbols"]:
                symbol_name = item["pair"]
                if symbol_name.endswith("USDT"):
                    binance_symbols.add(symbol_name)
            return sorted(list(binance_symbols))
        except BinanceAPIException as e:
            if e.code == -1003: # Too many requests
                print(f"Rate limit hit for getting all symbols. Retrying...")
                raise # Re-raise to trigger tenacity retry
            else:
                print(f"Error fetching crypto symbols from Binance: {e}")
                return []
        except Exception as e:
            print(f"Error fetching crypto symbols from Binance: {e}")
            return []
