import pytest
from unittest.mock import patch
from services.notification_service import NotificationService
from discord_webhook import DiscordEmbed

@pytest.fixture
def notification_service():
    """Fixture for a NotificationService with a dummy webhook url."""
    return NotificationService(webhook_url="https://dummy.url/webhook")

@patch('services.notification_service.DiscordWebhook')
def test_send_buy_trade_notification(mock_webhook_class, notification_service):
    """Test sending a BUY trade notification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_trade_notification(
        symbol='BTCUSDT',
        action='BUY',
        price=50000.0,
        units=0.1
    )

    mock_webhook_class.assert_called_once_with(url="https://dummy.url/webhook")
    mock_instance.add_embed.assert_called_once()
    
    embed_arg = mock_instance.add_embed.call_args[0][0]
    assert isinstance(embed_arg, DiscordEmbed)
    assert "BUY BTCUSDT" in embed_arg.title
    assert embed_arg.color == int("00ff00", 16)

@patch('services.notification_service.DiscordWebhook')
def test_send_sell_trade_notification(mock_webhook_class, notification_service):
    """Test sending a SELL trade notification with PnL."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_trade_notification(
        symbol='ETHUSDT',
        action='SELL',
        price=4000.0,
        units=1.0,
        reason='TAKE_PROFIT',
        pnl=500.0
    )

    mock_webhook_class.assert_called_once()
    mock_instance.add_embed.assert_called_once()
    embed_arg = mock_instance.add_embed.call_args[0][0]
    assert "SELL ETHUSDT" in embed_arg.title
    assert embed_arg.color == int("ff0000", 16)
    assert any(field['name'] == 'Profit/Loss' and '500.00' in field['value'] for field in embed_arg.fields)

@patch('services.notification_service.DiscordWebhook')
def test_send_list_change_notification(mock_webhook_class, notification_service):
    """Test sending a list change notification."""
    mock_instance = mock_webhook_class.return_value
    mock_instance.execute.return_value.status_code = 200

    notification_service.send_list_change_notification(
        added={'BTCUSDT', 'ETHUSDT'},
        removed={'ADAUSDT'}
    )

    mock_webhook_class.assert_called_once()
    mock_instance.add_embed.assert_called_once()
    embed_arg = mock_instance.add_embed.call_args[0][0]
    assert "Strong Target List Updated" in embed_arg.title
    assert embed_arg.color == int("0000ff", 16)
    assert any(field['name'] == '✅ Added' for field in embed_arg.fields)
    assert any(field['name'] == '❌ Removed' for field in embed_arg.fields)

def test_disabled_service():
    """Test that no webhook is called if the URL is not provided."""
    with patch('services.notification_service.DiscordWebhook') as mock_webhook:
        disabled_service = NotificationService(webhook_url=None)
        disabled_service.send_trade_notification('BTCUSDT', 'BUY', 50000, 0.1)
        mock_webhook.assert_not_called()
