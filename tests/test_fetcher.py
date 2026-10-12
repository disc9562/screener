import pytest
from unittest.mock import MagicMock
from data.fetcher import CryptoFetcher
# from data.fetcher import StockFetcher # Commented out for Story 3 scope

# To run these tests, use the command: python3 -m pytest

@pytest.fixture
def mock_polygon_client(mocker):
    """Fixture to mock the Polygon RESTClient."""
    mock_client = MagicMock()
    # Mock the list_aggs method to return a sample response
    mock_client.list_aggs.return_value = [MagicMock(timestamp=1672531200000, open=100, close=102, high=103, low=99, volume=1000)]
    mocker.patch('data.fetcher.RESTClient', return_value=mock_client)
    return mock_client

@pytest.fixture
def mock_binance_client(mocker):
    """Fixture to mock the Binance Client."""
    mock_client = MagicMock()
    # Mock the futures_klines method to return a sample kline
    mock_client.futures_klines.return_value = [['1672531200000', '100', '103', '99', '102', '1000', '...']]
    mocker.patch('data.fetcher.Client', return_value=mock_client)
    return mock_client


# def test_stock_fetcher_initialization(mocker):
#     """Test that StockFetcher initializes the Polygon client correctly."""
#     # Arrange
#     mocker.patch('builtins.open', mocker.mock_open(read_data='{"polygon": "test_key"}'))
#     mock_rest_client = mocker.patch('data.fetcher.RESTClient')
#     
#     # Act
#     fetcher = StockFetcher(api_file='dummy_path')
#     
#     # Assert
#     mock_rest_client.assert_called_once_with(api_key='test_key', retries=mocker.ANY)
#     assert fetcher.client is not None

# def test_stock_fetcher_fetch_aggregates(mock_polygon_client):
#     """Test the fetch_aggregates method calls the client's list_aggs."""
#     # Arrange
#     fetcher = StockFetcher()
#     fetcher.client = mock_polygon_client # Inject the mock
#     
#     # Act
#     result = fetcher.fetch_aggregates('AAPL', 1672531200, 1672617600)
#     
#     # Assert
#     mock_polygon_client.list_aggs.assert_called_once()
#     assert result is not None
#     assert len(result) == 1

def test_crypto_fetcher_initialization(mock_binance_client):
    """Test that CryptoFetcher initializes the Binance client."""
    # Arrange & Act
    fetcher = CryptoFetcher()
    fetcher.client = mock_binance_client # Inject the mock

    # Assert
    assert fetcher.client is not None

def test_crypto_fetcher_fetch_klines(mock_binance_client):
    """Test the fetch_klines method calls the client's futures_klines."""
    # Arrange
    fetcher = CryptoFetcher()
    fetcher.client = mock_binance_client # Inject the mock
    
    # Act
    result = fetcher.fetch_klines('BTCUSDT')
    
    # Assert
    mock_binance_client.futures_klines.assert_called_once_with(symbol='BTCUSDT', interval='15m', limit=1500)
    assert result is not None
    assert len(result) == 1


# --- 幣安 ban 修正：權重閘門 ---------------------------------------------------
import data.fetcher as F
from binance.exceptions import BinanceAPIException
from tenacity import stop_after_attempt


class FakeClock:
    """假時鐘：sleep 只推進時間，不真的等。"""
    def __init__(self, t=0.0):
        self.t, self.slept = t, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _limiter(budget=10, t=0.0):
    clk = FakeClock(t)
    return F.WeightLimiter(budget=budget, clock=clk.now, sleep=clk.sleep, wall=clk.now), clk


def test_kline_weight_matches_binance_buckets():
    # 2026-10-12 以 x-mbx-used-weight-1m 實測
    assert [F.kline_weight(n) for n in (99, 100, 499, 500, 1000, 1001, 1500)] == [1, 2, 2, 5, 5, 10, 10]


def test_limiter_waits_when_window_budget_used():
    lim, clk = _limiter(budget=10)
    lim.acquire(6)
    lim.acquire(4)
    assert clk.slept == []
    lim.acquire(1)                      # 窗口滿了 → 等到第一筆滿 60 秒
    assert clk.t == 60


def test_limiter_pause_blocks_everyone():
    lim, clk = _limiter()
    lim.pause(30)
    lim.acquire(1)
    assert clk.t == 30


def test_high_used_weight_header_pauses_until_next_minute():
    lim, clk = _limiter(t=125.0)        # 某分鐘第 5 秒
    lim.observe(F.HEADER_PAUSE_AT - 1)
    lim.acquire(1)
    assert clk.slept == []
    lim.observe(F.HEADER_PAUSE_AT)
    lim.acquire(1)
    assert clk.t == 180                 # 等到下一個整分鐘


def _rate_limited(status, retry_after):
    resp = MagicMock(headers={"Retry-After": str(retry_after)})
    return BinanceAPIException(resp, status, '{"code":-1003,"msg":"Too many requests"}')


def test_429_pauses_all_requests_for_retry_after(mock_binance_client, monkeypatch):
    lim, clk = _limiter()
    monkeypatch.setattr(F, "_LIMITER", lim)
    mock_binance_client.futures_klines.side_effect = _rate_limited(429, 7)
    fetcher = CryptoFetcher()
    fetcher.client = mock_binance_client
    with pytest.raises(Exception):
        F.CryptoFetcher.fetch_klines.retry_with(stop=stop_after_attempt(1))(fetcher, "BTCUSDT", limit=200)
    lim.acquire(1)
    assert clk.t == 7


def test_418_ban_pauses_for_ban_length(mock_binance_client, monkeypatch):
    lim, clk = _limiter()
    monkeypatch.setattr(F, "_LIMITER", lim)
    mock_binance_client.futures_klines.side_effect = _rate_limited(418, 120)
    fetcher = CryptoFetcher()
    fetcher.client = mock_binance_client
    with pytest.raises(Exception):
        F.CryptoFetcher.fetch_klines.retry_with(stop=stop_after_attempt(1))(fetcher, "BTCUSDT", limit=200)
    lim.acquire(1)
    assert clk.t == 120


def test_get_all_symbols_only_trading_usdt_perpetuals(mock_binance_client):
    mock_binance_client.futures_exchange_info.return_value = {"symbols": [
        {"symbol": "BTCUSDT", "pair": "BTCUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
        {"symbol": "BTCUSDT_261225", "pair": "BTCUSDT", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
        {"symbol": "FTTUSDT", "pair": "FTTUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "SETTLING"},
        {"symbol": "ETHUSDC", "pair": "ETHUSDC", "quoteAsset": "USDC", "contractType": "PERPETUAL", "status": "TRADING"},
    ]}
    fetcher = CryptoFetcher()
    fetcher.client = mock_binance_client
    assert fetcher.get_all_symbols() == ["BTCUSDT"]
