import pytest
import pandas as pd
import os
from services.position_manager import PositionManager
from services.notification_service import NotificationService # Import NotificationService for mocking

# Mock NotificationService for tests
@pytest.fixture
def mock_notification_service():
    """Mock NotificationService to prevent actual Discord calls during tests."""
    class MockNotificationService:
        def __init__(self, webhook_url=None):
            pass
        def send_trade_notification(self, *args, **kwargs):
            pass
        def send_list_change_notification(self, *args, **kwargs):
            pass
    return MockNotificationService()

@pytest.fixture
def strategy_config():
    """Fixture for a dummy strategy config."""
    return {
        "TOTAL_CAPITAL": 10000.0,
        "RISK_PER_TRADE_PERCENT": 0.01, # 1% risk
        "TRANSACTION_FEE_PERCENT": 0.0006 # 0.06% fee
    }

@pytest.fixture
def position_manager(tmp_path, mock_notification_service, strategy_config):
    """Fixture to create a PositionManager with a temporary CSV file and mock notification service."""
    test_csv_path = tmp_path / "test_positions.csv"
    # Ensure the file does not exist before initialization
    if os.path.exists(test_csv_path):
        os.remove(test_csv_path)
    manager = PositionManager(
        notification_service=mock_notification_service,
        strategy_config=strategy_config,
        csv_path=str(test_csv_path)
    )
    return manager

def test_open_new_position_units_and_fees(position_manager):
    """Test opening a new position with calculated units and initial PnL including fees."""
    # TOTAL_CAPITAL = 10000, RISK_PER_TRADE_PERCENT = 0.01
    # Risk amount = 10000 * 0.01 = 100
    # entry_price = 100, stop_loss = 95, price_diff = 5
    # units = 100 / 5 = 20
    # Opening fee = 100 * 20 * 0.0006 = 1.2
    # Initial PnL = -1.2

    signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 95,
        'take_profit': 120,
    }
    opened = position_manager.open_position('BTCUSDT', signal)
    assert opened is True
    assert len(position_manager.positions_df) == 1
    pos = position_manager.positions_df.iloc[0]
    assert pos['symbol'] == 'BTCUSDT'
    assert pos['status'] == 'OPEN'
    assert pos['entry_price'] == 100
    assert pos['stop_loss'] == 95
    assert pos['take_profit'] == 120
    assert pos['units'] == 20 # Assert calculated units
    assert pos['pnl'] == pytest.approx(-1.2) # Assert initial PnL with opening fee

def test_open_existing_position(position_manager):
    """Test that a new position is not opened if one already exists for the symbol."""
    signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 95,
        'take_profit': 120,
    }
    position_manager.open_position('BTCUSDT', signal)
    opened_again = position_manager.open_position('BTCUSDT', signal)
    assert opened_again is False
    assert len(position_manager.positions_df) == 1

def test_open_position_zero_price_diff(position_manager):
    """Test that position is not opened if entry_price equals stop_loss."""
    signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 100, # Same as entry_price
        'take_profit': 105,
    }
    opened = position_manager.open_position('ETHUSDT', signal)
    assert opened is False
    assert len(position_manager.positions_df) == 0 # No position should be opened

def test_open_position_invalid_units(position_manager):
    """Test that position is not opened if calculated units are invalid (e.g., <= 0)."""
    # Set risk percent to 0 to get 0 units
    position_manager.strategy_config['RISK_PER_TRADE_PERCENT'] = 0.0
    signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 90,
        'take_profit': 110,
    }
    opened = position_manager.open_position('LTCUSDT', signal)
    assert opened is False
    assert len(position_manager.positions_df) == 0 # No position should be opened

def test_update_position_take_profit_with_fees(position_manager):
    """Test that a position is closed correctly on take profit, with closing fees."""
    # entry_price = 100, units = 20 (from test_open_new_position_units_and_fees)
    # take_profit = 110
    # PnL before closing fee = (110 - 100) * 20 = 200
    # Closing fee = 110 * 20 * 0.0006 = 1.32
    # Final PnL = 200 - 1.32 = 198.68
    open_signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 95,
        'take_profit': 110,
    }
    position_manager.open_position('BTCUSDT', open_signal)
    
    latest_kline = pd.Series({'High': 112, 'Low': 98, 'Close': 111, 'Timestamp': pd.Timestamp.now()})
    position_manager.update_positions({'BTCUSDT': latest_kline})
    
    pos = position_manager.positions_df.iloc[0]
    assert pos['status'] == 'CLOSED'
    assert pos['exit_reason'] == 'TAKE_PROFIT'
    assert pos['pnl'] == pytest.approx(198.68) # Assert PnL with closing fee

def test_update_position_stop_loss_with_fees(position_manager):
    """Test that a position is closed correctly on stop loss, with closing fees."""
    # entry_price = 100, units = 20 (from test_open_new_position_units_and_fees)
    # stop_loss = 95
    # PnL before closing fee = (95 - 100) * 20 = -100
    # Closing fee = 95 * 20 * 0.0006 = 1.14
    # Final PnL = -100 - 1.14 = -101.14
    open_signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 95,
        'take_profit': 110,
    }
    position_manager.open_position('BTCUSDT', open_signal)
    
    latest_kline = pd.Series({'High': 102, 'Low': 94, 'Close': 96, 'Timestamp': pd.Timestamp.now()})
    position_manager.update_positions({'BTCUSDT': latest_kline})
    
    pos = position_manager.positions_df.iloc[0]
    assert pos['status'] == 'CLOSED'
    assert pos['exit_reason'] == 'STOP_LOSS'
    assert pos['pnl'] == pytest.approx(-101.14) # Assert PnL with closing fee

def test_update_floating_pnl_with_fees(position_manager):
    """Test that floating PnL is updated correctly for an open position, including initial opening fee."""
    # entry_price = 100, units = 20 (from test_open_new_position_units_and_fees)
    # Initial PnL = -1.2 (from test_open_new_position_units_and_fees)
    # Floating PnL from price change = (105 - 100) * 20 = 100
    # Total PnL = 100 - 1.2 = 98.8
    open_signal = {
        'timestamp': pd.Timestamp.now(),
        'entry_price': 100,
        'stop_loss': 90,
        'take_profit': 120,
    }
    position_manager.open_position('BTCUSDT', open_signal)
    
    latest_kline = pd.Series({'High': 108, 'Low': 102, 'Close': 105, 'Timestamp': pd.Timestamp.now()})
    position_manager.update_positions({'BTCUSDT': latest_kline})
    
    pos = position_manager.positions_df.iloc[0]
    assert pos['status'] == 'OPEN'
    assert pos['pnl'] == pytest.approx(49.4) # Assert floating PnL with opening fee
