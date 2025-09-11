import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import sys
import os
import argparse # Added for argparse.ArgumentParser
import queue # Added for queue.Empty

# Add the project root to the sys.path to allow imports from config, services, etc.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import main # Import main function
from config import get_strategy_config # Import get_strategy_config to mock it

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
            "TARGET_FETCH_TIMES": ["08:00", "20:00"]
        }
        yield mock_config

# Mock external dependencies to prevent actual network calls or file operations
@pytest.fixture(autouse=True)
def mock_external_dependencies():
    with patch('main.StrongTargetScreener') as MockScreener, \
         patch('main.WebSocketManager') as MockWSManager, \
         patch('main.NotificationService') as MockNotificationService, \
         patch('main.PositionManager') as MockPositionManager, \
         patch('main.transform_crypto_data') as MockTransformData:
        
        # Configure mocks
        MockScreener.return_value.run_screener.return_value = ['BTCUSDT'] # Default return for screener
        MockWSManager.return_value.get_message.side_effect = queue.Empty # Make get_message return immediately
        MockWSManager.return_value.stop.return_value = None # Ensure stop doesn't block
        MockWSManager.return_value.start.return_value = None # Ensure start doesn't block
        
        yield MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData


@pytest.fixture(autouse=True)
def clear_kline_cache():
    """Clears the kline_cache global variable in main.py before each test."""
    import main
    main.kline_cache = {}
    yield




def test_fetch_now_argument_triggers_immediate_fetch(mock_sys_argv, mock_get_strategy_config, mock_external_dependencies):
    """
    Tests that --fetch-now argument triggers an immediate strong target fetch.
    """
    test_args = ['--fetch-now', '--timeout', '1']
    
    MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData = mock_external_dependencies

    # Create a new ArgumentParser for the test and parse the test_args
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')
    args = parser.parse_args(test_args) # Pass args directly

    try:
        main(args) # Call main with the parsed args
    except SystemExit as e:
        assert e.code == 1 # Expecting sys.exit(1) for validation error

    # Assert that run_screener was called at least once
    MockScreener.return_value.run_screener.assert_called_once()
    # Assert that notification for general targets was sent
    MockNotificationService.return_value.send_list_change_notification.assert_called_once_with(
        added={'BTCUSDT'}, removed=set(), webhook_type="general_targets"
    )

@patch('main.datetime')
def test_scheduled_fetch_at_correct_time(mock_datetime, mock_sys_argv, mock_get_strategy_config, mock_external_dependencies):
    """
    Tests that scheduled fetch occurs at the correct time.
    """
    MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData = mock_external_dependencies

    # Create a new ArgumentParser for the test
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')

    # Set current time to just before 8:00
    mock_datetime.now.return_value = datetime(2025, 1, 1, 7, 59, 59)
    mock_datetime.side_effect = lambda: datetime(2025, 1, 1, 7, 59, 59) # For timedelta calculations

    # Run main for a short period
    try:
        main(parser.parse_args(['--timeout', '1']))
    except SystemExit as e:
        assert e.code == 1 # Expecting sys.exit(1) for validation error

    # Assert screener was NOT called yet
    MockScreener.return_value.run_screener.assert_not_called()

    # Set current time to 8:00
    mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 0, 0)
    mock_datetime.side_effect = lambda: datetime(2025, 1, 1, 8, 0, 0)

    # Run main again for a short period
    try:
        main(parser.parse_args(['--timeout', '1']))
    except SystemExit as e:
        assert e.code == 1 # Expecting sys.exit(1) for validation error

    # Assert screener was called once for the 8:00 schedule
    MockScreener.return_value.run_screener.assert_called_once()
    MockNotificationService.return_value.send_list_change_notification.assert_called_once_with(
        added={'BTCUSDT'}, removed=set(), webhook_type="general_targets"
    )

@patch('main.datetime')
def test_scheduled_fetch_only_once_per_time_per_day(mock_datetime, mock_sys_argv, mock_get_strategy_config, mock_external_dependencies):
    """
    Tests that scheduled fetch occurs only once per scheduled time per day.
    """
    MockScreener, MockWSManager, MockNotificationService, MockPositionManager, MockTransformData = mock_external_dependencies

    # Create a new ArgumentParser for the test
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')

    # Set current time to 8:00
    mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 0, 0)
    mock_datetime.side_effect = lambda: datetime(2025, 1, 1, 8, 0, 0)

    # Run main for a short period (first fetch)
    try:
        main(parser.parse_args(['--timeout', '1']))
    except SystemExit as e:
        assert e.code == 1 # Expecting sys.exit(1) for validation error

    # Reset mocks for second run
    MockScreener.return_value.run_screener.reset_mock()
    MockNotificationService.return_value.send_list_change_notification.reset_mock()

    # Run main again at 8:01 (should not fetch again)
    mock_datetime.now.return_value = datetime(2025, 1, 1, 8, 1, 0)
    mock_datetime.side_effect = lambda: datetime(2025, 1, 1, 8, 1, 0)
    try:
        main(parser.parse_args(['--timeout', '1']))
    except SystemExit as e:
        assert e.code == 1 # Expecting sys.exit(1) for validation error

    # Assert screener was NOT called again
    MockScreener.return_value.run_screener.assert_not_called()
    MockNotificationService.return_value.send_list_change_notification.assert_not_called()

    # Move to next day 8:00 (should fetch again)
    mock_datetime.now.return_value = datetime(2025, 1, 2, 8, 0, 0)
    mock_datetime.side_effect = lambda: datetime(2025, 1, 2, 8, 0, 0)
    try:
        main(parser.parse_args(['--timeout', '1']))
    except SystemExit as e:
        assert e.code == 1 # Expecting sys.exit(1) for validation error

    # Assert screener was called again
    MockScreener.return_value.run_screener.assert_called_once()
    MockNotificationService.return_value.send_list_change_notification.assert_called_once()