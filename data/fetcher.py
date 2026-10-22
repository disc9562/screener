import logging
import time
import json
from pathlib import Path
from binance import Client
from binance.exceptions import BinanceAPIException
import requests # New import for handling connection errors
from polygon import RESTClient
from urllib3.util.retry import Retry
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryError
import threading
from collections import deque

logger = logging.getLogger(__name__)

# Binance USDⓈ-M futures: 2400 request weight per IP per minute (exchangeInfo rateLimits).
# Going over returns 429; ignoring 429s escalates to a 418 IP ban (2 minutes up to 3 days).
WEIGHT_BUDGET_PER_MIN = 1200   # this process uses at most half, the rest is left for other programs on the same IP
HEADER_PAUSE_AT = 1800         # Binance-reported usage for this IP (x-mbx-used-weight-1m) at/above this -> wait for the next minute


def kline_weight(limit):
    """Weight of one futures_klines call (measured 2026-10-12 via x-mbx-used-weight-1m)."""
    return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10


class WeightLimiter:
    """Request-weight gate shared by every thread: 60s sliding-window budget, plus a global pause after 429/418."""

    def __init__(self, budget=WEIGHT_BUDGET_PER_MIN, clock=time.monotonic, sleep=time.sleep, wall=time.time):
        self.budget, self.clock, self.sleep, self.wall = budget, clock, sleep, wall
        self.lock = threading.Lock()
        self.window = deque()          # (time, weight)
        self.paused_until = 0.0

    def acquire(self, weight):
        while True:
            with self.lock:
                now = self.clock()
                while self.window and now - self.window[0][0] >= 60:
                    self.window.popleft()
                if now < self.paused_until:
                    wait = self.paused_until - now
                elif sum(w for _, w in self.window) + weight <= self.budget:
                    self.window.append((now, weight))
                    return
                else:
                    wait = 60 - (now - self.window[0][0])
            self.sleep(wait)

    def pause(self, seconds):
        with self.lock:
            self.paused_until = max(self.paused_until, self.clock() + seconds)

    def observe(self, used_weight):
        """Binance's per-minute counter for the whole IP (other programs included) is near the cap -> wait for the next minute."""
        if used_weight >= HEADER_PAUSE_AT:
            self.pause(60 - self.wall() % 60)


_LIMITER = WeightLimiter()

_NETWORK_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


def _give_up(default):
    """tenacity 重試用完：網路錯誤 → 回傳 default（呼叫端照舊當成「沒資料」）；限速等其他錯誤 → 照原本拋 RetryError。"""
    def callback(state):
        exc = state.outcome.exception()
        if isinstance(exc, _NETWORK_ERRORS):
            logger.warning(f"Network error after {state.attempt_number} attempts: {exc}")
            return default
        raise RetryError(state.outcome)
    return callback


def _rate_limited(exc):
    """429/418 (code -1003): pause every thread for Retry-After before tenacity retries."""
    if exc.status_code in (418, 429) or exc.code == -1003:
        _LIMITER.pause(_retry_after(exc))
        return True
    return False


def _retry_after(exc, default=60):
    try:
        return int(exc.response.headers.get("Retry-After", default))
    except (AttributeError, TypeError, ValueError):
        return default


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
            logger.error(f"Error initializing CryptoFetcher: {e}")
            self.client = None

    def _request(self, weight, fn, **kwargs):
        """Every Binance REST call goes through here: wait for weight budget, then record the IP's used weight reported by Binance."""
        _LIMITER.acquire(weight)
        result = fn(**kwargs)
        used = getattr(getattr(self.client, "response", None), "headers", {}).get("x-mbx-used-weight-1m")
        if used is not None:
            _LIMITER.observe(int(used))
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception_type((BinanceAPIException, requests.exceptions.ConnectionError, requests.exceptions.Timeout)),
           retry_error_callback=_give_up(None))
    def fetch_klines(self, symbol, interval="15m", limit=1500):
        if not self.client:
            return None

        try:
            klines = self._request(kline_weight(limit), self.client.futures_klines,
                                   symbol=symbol, interval=interval, limit=limit)
            return klines
        except BinanceAPIException as e:
            if _rate_limited(e):
                logger.warning(f"Rate limit hit for {symbol} (HTTP {e.status_code}). Pausing all requests {_retry_after(e)}s, then retrying...")
                raise # Re-raise to trigger tenacity retry
            else:
                logger.error(f"Error fetching klines for {symbol}: {e}")
                return None
        except _NETWORK_ERRORS:
            raise                       # 交給 tenacity 重試（原本被下面的 except 吞掉，重試從沒觸發過）
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception_type((BinanceAPIException, requests.exceptions.ConnectionError, requests.exceptions.Timeout)),
           retry_error_callback=_give_up({}))
    def fetch_prices(self):
        """所有永續合約的最新價格 {symbol: price}，一次請求（權重 2）；失敗回傳 {}。"""
        if not self.client:
            return {}
        try:
            rows = self._request(2, self.client.futures_symbol_ticker)
            return {r["symbol"]: float(r["price"]) for r in rows}
        except BinanceAPIException as e:
            if _rate_limited(e):
                logger.warning(f"Rate limit hit for prices (HTTP {e.status_code}). Pausing all requests {_retry_after(e)}s, then retrying...")
                raise
            logger.error(f"Error fetching prices: {e}")
            return {}
        except _NETWORK_ERRORS:
            raise
        except Exception as e:
            logger.error(f"Error fetching prices: {e}")
            return {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception_type((BinanceAPIException, requests.exceptions.ConnectionError, requests.exceptions.Timeout)),
           retry_error_callback=_give_up([]))
    def get_all_symbols(self):
        """USDT-margined perpetuals that are currently TRADING (excludes delisted/settled contracts and quarterlies)."""
        if not self.client:
            return []
        
        try:
            binance_response = self._request(1, self.client.futures_exchange_info)
            return sorted(item["symbol"] for item in binance_response["symbols"]
                          if item.get("quoteAsset") == "USDT" and item.get("contractType") == "PERPETUAL"
                          and item.get("status") == "TRADING")
        except BinanceAPIException as e:
            if _rate_limited(e):
                logger.warning("Rate limit hit for getting all symbols. Retrying...")
                raise # Re-raise to trigger tenacity retry
            else:
                logger.error(f"Error fetching crypto symbols from Binance: {e}")
                return []
        except _NETWORK_ERRORS:
            raise
        except Exception as e:
            logger.error(f"Error fetching crypto symbols from Binance: {e}")
            return []

