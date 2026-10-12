import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import sys
import os
import argparse
import queue
import gc

# Add the project root to the sys.path to allow imports from config, services, etc.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import run_app, main
from config import get_strategy_config

# Mock sys.argv for command-line arguments
@pytest.fixture
def mock_sys_argv():
    original_argv = sys.argv
    sys.argv = [original_argv[0]] # Start with just the script name
    yield sys.argv
    sys.argv = original_argv

# Mock get_strategy_config to control config values
@pytest.fixture
def mock_get_strategy_config():
    with patch('main.get_strategy_config') as mock_config:
        mock_config.return_value = {
            "DISCORD_WEBHOOK_URL_VOLUME_ON": "http://webhook_on",
            "DISCORD_WEBHOOK_URL_VOLUME_OFF": "http://webhook_off",
            "DISCORD_WEBHOOK_URL_GENERAL_TARGETS": "http://webhook_general",
            "TARGET_FETCH_TIMES": ["08:00", "20:00"],
            "TEST_COIN_SUBSET": ["BTCUSDT", "ETHUSDT"]
        }
        yield mock_config

# Mock external dependencies to prevent actual network calls or file operations
@pytest.fixture(autouse=True)
def mock_external_dependencies():
    with patch('main.StrongTargetScreener') as MockScreener, \
         patch('main.WebSocketManager') as MockWSManager, \
         patch('main.NotificationService') as MockNotificationService, \
         patch('main.PositionManager') as MockPositionManager, \
         patch('main.transform_crypto_data') as MockTransformData, \
         patch('main.sys.exit') as mock_exit:
        
        # Configure mocks
        MockScreener.return_value.run_screener.return_value = ['BTCUSDT'] # Default return for screener
        MockWSManager.return_value.get_message.side_effect = queue.Empty # Make get_message return immediately
        MockWSManager.return_value.stop.return_value = None # Ensure stop doesn't block
        MockWSManager.return_value.start.return_value = None # Ensure start doesn't block
        
        yield MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData, mock_exit


@pytest.fixture(autouse=True)
def clear_kline_cache():
    """Clears the kline_cache global variable in main.py before each test."""
    import main
    main.kline_cache = {}
    yield

@pytest.fixture(autouse=True)
def cleanup_memory():
    """Fixture to clean up memory after each test."""
    yield
    gc.collect()


@patch('main.run_app')
def test_main_calls_run_app(mock_run_app, mock_sys_argv):
    """
    Tests that main() function calls run_app() with the correct arguments.
    """
    mock_sys_argv.extend(['--fetch-now', '--timeout', '1'])
    main()
    mock_run_app.assert_called_once()
    args = mock_run_app.call_args[0][0]
    assert args.fetch_now is True
    assert args.timeout == 1

@patch('main.datetime')
def test_scheduled_fetch_at_correct_time(mock_datetime, mock_sys_argv, mock_get_strategy_config, mock_external_dependencies):
    """
    Tests that scheduled fetch occurs at the correct time.
    """
    MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData, mock_exit = mock_external_dependencies

    # Create a new ArgumentParser for the test
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')
    parser.add_argument('--local-test', action='store_true', help='Enable local test mode (e.g., subset of coins, no WebSocket).')

    # Set current time to just before 8:00
    mock_datetime.now.return_value = datetime(2025, 1, 1, 7, 59, 59)
    
    # Run main for a short period
    run_app(parser.parse_args(['--timeout', '1']))

    # Assert screener was NOT called yet
    MockScreener.return_value.run_screener.assert_not_called()

    # Set current time to 8:00
    mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 0, 0)

    # Run main again for a short period
    run_app(parser.parse_args(['--timeout', '1']))

    # Assert screener was called once for the 8:00 schedule
    MockScreener.return_value.run_screener.assert_called()
    MockNotificationService.return_value.send_list_change_notification.assert_called()

@patch('main.datetime')
def test_scheduled_fetch_only_once_per_time_per_day(mock_datetime, mock_sys_argv, mock_get_strategy_config, mock_external_dependencies):
    """
    Tests that scheduled fetch occurs only once per scheduled time per day.
    """
    MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData, mock_exit = mock_external_dependencies

    # Create a new ArgumentParser for the test
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')
    parser.add_argument('--local-test', action='store_true', help='Enable local test mode (e.g., subset of coins, no WebSocket).')

    # Set current time to 8:00
    mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 0, 0)

    # Run main for a short period (first fetch)
    run_app(parser.parse_args(['--timeout', '1']))

    # Reset mocks for second run
    MockScreener.return_value.run_screener.reset_mock()
    MockNotificationService.return_value.send_list_change_notification.reset_mock()

    # Run main again at 8:01 (should not fetch again)
    mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 1, 0)
    run_app(parser.parse_args(['--timeout', '1']))

    # Assert screener was NOT called again
    MockScreener.return_value.run_screener.assert_not_called()
    MockNotificationService.return_value.send_list_change_notification.assert_not_called()

    # Move to next day 8:00 (should fetch again)
    mock_datetime.now.return_value = datetime(2025, 1, 2, 8, 0, 0)
    run_app(parser.parse_args(['--timeout', '1']))

    # Assert screener was called again
    MockScreener.return_value.run_screener.assert_called_once()
    MockNotificationService.return_value.send_list_change_notification.assert_called_once()