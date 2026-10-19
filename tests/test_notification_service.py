import pytest
from unittest.mock import patch
from services.notification_service import NotificationService

@pytest.fixture
def notification_service():
    """Fixture for a NotificationService with dummy webhook URLs."""
    webhook_urls_map = {
        "volume_on": "https://dummy.url/webhook_on",
        "volume_off": "https://dummy.url/webhook_off",
        "default": "https://dummy.url/webhook_default",
        "general_targets": "https://dummy.url/webhook_general",
    }
    return NotificationService(webhook_urls_map=webhook_urls_map)

@patch('services.notification_service.DiscordWebhook')
def test_send_buy_trade_notification_volume_on(mock_webhook_class, notification_service):
    """Test sending a BUY trade notification with volume ON and correct routing/identification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_trade_notification(
        symbol='BTCUSDT',
        action='BUY',
        price=50000.0,
        units=0.1,
        is_volume_on=True
    )

    mock_webhook_class.assert_called_once()
    kwargs = mock_webhook_class.call_args.kwargs
    assert kwargs["url"] == "https://dummy.url/webhook_on"
    assert "BUY BTCUSDT" in kwargs["content"]
    assert "[Volume ON]" in kwargs["content"]

@patch('services.notification_service.DiscordWebhook')
def test_send_buy_trade_notification_volume_off(mock_webhook_class, notification_service):
    """Test sending a BUY trade notification with volume OFF and correct routing/identification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_trade_notification(
        symbol='ETHUSDT',
        action='BUY',
        price=4000.0,
        units=1.0,
        is_volume_on=False
    )

    mock_webhook_class.assert_called_once()
    kwargs = mock_webhook_class.call_args.kwargs
    assert kwargs["url"] == "https://dummy.url/webhook_off"
    assert "BUY ETHUSDT" in kwargs["content"]
    assert "[Volume OFF]" in kwargs["content"]

@patch('services.notification_service.DiscordWebhook')
def test_send_sell_trade_notification_volume_on(mock_webhook_class, notification_service):
    """Test sending a SELL trade notification with PnL, volume ON, and correct routing/identification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_trade_notification(
        symbol='ETHUSDT',
        action='SELL',
        price=4000.0,
        units=1.0,
        reason='TAKE_PROFIT',
        pnl=500.0,
        is_volume_on=True
    )

    mock_webhook_class.assert_called_once()
    kwargs = mock_webhook_class.call_args.kwargs
    assert kwargs["url"] == "https://dummy.url/webhook_on"
    assert "SELL ETHUSDT" in kwargs["content"]
    assert "[Volume ON]" in kwargs["content"]
    assert "Profit/Loss: 500.00 USD" in kwargs["content"]

@patch('services.notification_service.DiscordWebhook')
def test_send_sell_trade_notification_volume_off(mock_webhook_class, notification_service):
    """Test sending a SELL trade notification with PnL, volume OFF, and correct routing/identification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_trade_notification(
        symbol='ETHUSDT',
        action='SELL',
        price=4000.0,
        units=1.0,
        reason='TAKE_PROFIT',
        pnl=500.0,
        is_volume_on=False
    )

    mock_webhook_class.assert_called_once()
    kwargs = mock_webhook_class.call_args.kwargs
    assert kwargs["url"] == "https://dummy.url/webhook_off"
    assert "SELL ETHUSDT" in kwargs["content"]
    assert "[Volume OFF]" in kwargs["content"]
    assert "Profit/Loss: 500.00 USD" in kwargs["content"]

@patch('services.notification_service.DiscordWebhook')
def test_send_list_change_notification_volume_on(mock_webhook_class, notification_service):
    """Test sending a list change notification with volume ON and correct routing/identification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_list_change_notification(
        added={'BTCUSDT', 'ETHUSDT'},
        removed={'ADAUSDT'},
        is_volume_on=True
    )

    mock_webhook_class.assert_called_once()
    kwargs = mock_webhook_class.call_args.kwargs
    assert kwargs["url"] == "https://dummy.url/webhook_on"
    assert "Strong Target List Updated [Volume ON]" in kwargs["content"]
    assert "✅ Added:" in kwargs["content"]
    assert "❌ Removed:" in kwargs["content"]

@patch('services.notification_service.DiscordWebhook')
def test_send_list_change_notification_volume_off(mock_webhook_class, notification_service):
    """Test sending a list change notification with volume OFF and correct routing/identification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_list_change_notification(
        added={'LTCUSDT'},
        removed={'XRPUSDT'},
        is_volume_on=False
    )

    mock_webhook_class.assert_called_once()
    kwargs = mock_webhook_class.call_args.kwargs
    assert kwargs["url"] == "https://dummy.url/webhook_off"
    assert "Strong Target List Updated [Volume OFF]" in kwargs["content"]
    assert "✅ Added:" in kwargs["content"]
    assert "❌ Removed:" in kwargs["content"]

@patch('services.notification_service.DiscordWebhook')
def test_disabled_service_no_webhook_url(mock_webhook_class):
    """Test that no webhook is called if the URL for a specific type is not provided."""
    webhook_urls_map = {
        "volume_on": "https://dummy.url/webhook_on",
        "volume_off": None # Simulate missing webhook
    }
    disabled_service = NotificationService(webhook_urls_map=webhook_urls_map)
    
    # This call should not trigger a webhook
    disabled_service.send_trade_notification('BTCUSDT', 'BUY', 50000, 0.1, is_volume_on=False)
    mock_webhook_class.assert_not_called() # Assert that DiscordWebhook was not instantiated
    
    # This call should trigger a webhook
    disabled_service.send_trade_notification('ETHUSDT', 'BUY', 4000, 1.0, is_volume_on=True)
    mock_webhook_class.assert_called_once()
    assert mock_webhook_class.call_args.kwargs["url"] == "https://dummy.url/webhook_on"

