import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from binance.exceptions import BinanceAPIException
from services.order_execution_service import OrderExecutionService
from services.position_manager import PositionManager


class MockNotification:
    """Stub notification service that swallows all calls."""
    def send_trade_notification(self, *args, **kwargs):
        pass


BTCUSDT_EXCHANGE_INFO = {
    'symbols': [{
        'symbol': 'BTCUSDT',
        'quantityPrecision': 3,
        'pricePrecision': 2,
        'filters': [
            {'filterType': 'LOT_SIZE', 'stepSize': '0.001'},
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
        ],
    }],
}

DEFAULT_STRATEGY_CONFIG = {
    'TOTAL_CAPITAL': 10000.0,
    'RISK_PER_TRADE_PERCENT': 0.01,
    'TRANSACTION_FEE_PERCENT': 0.0006,
    'use_volume_condition': False,
}


@pytest.fixture
def mock_client():
    """Patch binance.Client so no real connection is made."""
    with patch('services.order_execution_service.Client') as MockClient:
        instance = MagicMock()
        MockClient.return_value = instance
        instance.futures_account_balance.return_value = [
            {'asset': 'USDT', 'balance': '500.00000000'}
        ]
        instance.futures_exchange_info.return_value = BTCUSDT_EXCHANGE_INFO
        yield instance


@pytest.fixture
def service(mock_client):
    """Create an OrderExecutionService with mocked Client."""
    return OrderExecutionService(api_key='test_key', api_secret='test_secret')


class TestInit:
    def test_logs_balance_on_init(self, mock_client):
        """Service should log account balance on initialization."""
        service = OrderExecutionService('key', 'secret')
        mock_client.futures_account_balance.assert_called_once()

    def test_init_survives_balance_error(self, mock_client):
        """Service should not crash if balance fetch fails."""
        mock_client.futures_account_balance.side_effect = Exception("connection error")
        service = OrderExecutionService('key', 'secret')
        assert service is not None


class TestGetSymbolInfo:
    def test_returns_symbol_info(self, service, mock_client):
        info = service.get_symbol_info('BTCUSDT')
        assert info is not None
        assert info['quantityPrecision'] == 3
        assert info['pricePrecision'] == 2
        assert info['stepSize'] == 0.001
        assert info['tickSize'] == 0.01

    def test_caches_symbol_info(self, service, mock_client):
        service.get_symbol_info('BTCUSDT')
        service.get_symbol_info('BTCUSDT')
        # futures_exchange_info should only be called once (cached)
        assert mock_client.futures_exchange_info.call_count == 1

    def test_returns_none_for_unknown_symbol(self, service, mock_client):
        info = service.get_symbol_info('UNKNOWNUSDT')
        assert info is None

    def test_returns_none_on_api_error(self, service, mock_client):
        mock_client.futures_exchange_info.side_effect = BinanceAPIException(
            MagicMock(status_code=500, text='error', headers={}), 500, 'error'
        )
        info = service.get_symbol_info('BTCUSDT')
        assert info is None


class TestPlaceMarketOrder:
    def test_success(self, service, mock_client):
        mock_client.futures_create_order.return_value = {'orderId': 12345, 'status': 'FILLED'}

        result = service.place_market_order('BTCUSDT', 'BUY', 0.5)

        assert result is not None
        assert result['orderId'] == 12345
        mock_client.futures_create_order.assert_called_once_with(
            symbol='BTCUSDT',
            side='BUY',
            type='MARKET',
            quantity=0.5,
        )

    def test_failure_returns_none(self, service, mock_client):
        mock_client.futures_create_order.side_effect = BinanceAPIException(
            MagicMock(status_code=400, text='error', headers={}), 400, 'error'
        )
        result = service.place_market_order('BTCUSDT', 'BUY', 0.5)
        assert result is None


class TestPlaceStopLossOrder:
    def test_success(self, service, mock_client):
        mock_client.futures_create_order.return_value = {'orderId': 12346}

        result = service.place_stop_loss_order('BTCUSDT', 'SELL', 0.5, 60000.0)

        assert result is not None
        mock_client.futures_create_order.assert_called_once_with(
            symbol='BTCUSDT',
            side='SELL',
            type='STOP_MARKET',
            quantity=0.5,
            stopPrice=60000.0,
            closePosition='false',
        )

    def test_failure_returns_none(self, service, mock_client):
        mock_client.futures_create_order.side_effect = Exception("network error")
        result = service.place_stop_loss_order('BTCUSDT', 'SELL', 0.5, 60000.0)
        assert result is None


class TestPlaceTakeProfitOrder:
    def test_success(self, service, mock_client):
        mock_client.futures_create_order.return_value = {'orderId': 12347}

        result = service.place_take_profit_order('BTCUSDT', 'SELL', 0.5, 80000.0)

        assert result is not None
        mock_client.futures_create_order.assert_called_once_with(
            symbol='BTCUSDT',
            side='SELL',
            type='TAKE_PROFIT_MARKET',
            quantity=0.5,
            stopPrice=80000.0,
            closePosition='false',
        )


class TestCancelOpenOrders:
    def test_success(self, service, mock_client):
        mock_client.futures_cancel_all_open_orders.return_value = {'code': 200}
        result = service.cancel_open_orders('BTCUSDT')
        assert result is not None

    def test_failure_returns_none(self, service, mock_client):
        mock_client.futures_cancel_all_open_orders.side_effect = BinanceAPIException(
            MagicMock(status_code=400, text='error', headers={}), 400, 'error'
        )
        result = service.cancel_open_orders('BTCUSDT')
        assert result is None


class TestClosePosition:
    def test_cancels_orders_then_sells(self, service, mock_client):
        mock_client.futures_cancel_all_open_orders.return_value = {'code': 200}
        mock_client.futures_create_order.return_value = {'orderId': 12348}

        result = service.close_position('BTCUSDT', 0.5)

        assert result is not None
        mock_client.futures_cancel_all_open_orders.assert_called_once_with(symbol='BTCUSDT')
        mock_client.futures_create_order.assert_called_once_with(
            symbol='BTCUSDT',
            side='SELL',
            type='MARKET',
            quantity=0.5,
        )


class TestRoundQuantity:
    def test_rounds_down_to_step_size(self, service):
        # stepSize=0.001 for BTCUSDT, so 0.12345 -> 0.123
        result = service._round_quantity('BTCUSDT', 0.12345)
        assert result == 0.123

    def test_handles_unknown_symbol(self, service):
        # Falls back to 3 decimal places
        result = service._round_quantity('UNKNOWNUSDT', 0.12345)
        assert result == 0.123


class TestPositionManagerIntegration:
    """Test that PositionManager correctly calls OrderExecutionService."""

    def _make_position_manager(self, tmp_path, order_execution_service=None):
        """Helper to create a PositionManager with test defaults."""
        csv_path = str(tmp_path / "test_positions.csv")
        return PositionManager(
            notification_service=MockNotification(),
            strategy_config=DEFAULT_STRATEGY_CONFIG.copy(),
            csv_path=csv_path,
            order_execution_service=order_execution_service,
        )

    def _make_signal(self, entry_price=100.0, stop_loss=95.0, take_profit=200.0):
        return {
            'timestamp': pd.Timestamp.now(),
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
        }

    def test_open_position_places_orders(self, service, mock_client, tmp_path):
        """When order_execution_service is provided, open_position should place orders."""
        mock_client.futures_create_order.return_value = {'orderId': 99999}
        pm = self._make_position_manager(tmp_path, order_execution_service=service)

        result = pm.open_position('BTCUSDT', self._make_signal())

        assert result is True
        assert mock_client.futures_create_order.call_count == 3

    def test_open_position_works_without_service(self, tmp_path):
        """Without order_execution_service, open_position works as before (CSV only)."""
        pm = self._make_position_manager(tmp_path)

        result = pm.open_position('BTCUSDT', self._make_signal())

        assert result is True
        assert len(pm.positions_df) == 1

    def test_update_positions_closes_on_testnet(self, service, mock_client, tmp_path):
        """When TP/SL triggers, close_position should be called on testnet."""
        mock_client.futures_create_order.return_value = {'orderId': 99999}
        mock_client.futures_cancel_all_open_orders.return_value = {'code': 200}
        pm = self._make_position_manager(tmp_path, order_execution_service=service)

        pm.open_position('BTCUSDT', self._make_signal(take_profit=110.0))
        mock_client.futures_create_order.reset_mock()
        mock_client.futures_cancel_all_open_orders.reset_mock()

        latest_kline = pd.Series({'High': 112, 'Low': 98, 'Close': 111, 'Datetime': pd.Timestamp.now()})
        pm.update_positions({'BTCUSDT': latest_kline})

        mock_client.futures_cancel_all_open_orders.assert_called_once_with(symbol='BTCUSDT')
        mock_client.futures_create_order.assert_called_once()
