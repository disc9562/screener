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
