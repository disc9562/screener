import logging
from discord_webhook import DiscordWebhook, DiscordEmbed

class NotificationService:
    def __init__(self, webhook_urls_map: dict):
        self.webhook_urls_map = webhook_urls_map

    def _send_embed_notification(self, embed: DiscordEmbed, webhook_type: str = 'default', content: str = None):
        """Sends a notification with a prepared DiscordEmbed object or plain text content to the specified webhook type."""
        webhook_url = self.webhook_urls_map.get(webhook_type)
        if not webhook_url:
            logging.warning(f"Discord webhook URL for type '{webhook_type}' is not set. Notification will not be sent.")
            return

        try:
            if content:
                if len(content) > 2000:
                    logging.warning(f"Notification content truncated to 2000 characters for webhook type '{webhook_type}'.")
                    content = content[:2000]
                webhook = DiscordWebhook(url=webhook_url, content=content)
            else:
                webhook = DiscordWebhook(url=webhook_url)
                webhook.add_embed(embed)
            response = webhook.execute()
            if response.status_code not in [200, 204]:
                logging.error(f"Discord webhook for type '{webhook_type}' failed with status {response.status_code}: {response.content}")
        except Exception as e:
            logging.error(f"Error sending Discord notification for type '{webhook_type}': {e}")

    def send_trade_notification(self, symbol, action, price, units, reason=None, pnl=None, stop_loss_price=None, is_volume_on: bool = False):
        """Formats and sends a trade event notification."""
        # AC4: Add identification to notification content
        strategy_tag = "[Volume ON]" if is_volume_on else "[Volume OFF]"
        message_parts = [
            f"Trade Executed {strategy_tag}: {action.upper()} {symbol}",
            f"Price: {price:.4f}",
            f"Units: {units:.4f}"
        ]
        if action == "BUY" and stop_loss_price is not None:
            message_parts.append(f"Stop Loss: {stop_loss_price:.4f}")
        if reason:
            message_parts.append(f"Reason: {reason}")
        if pnl is not None:
            message_parts.append(f"Profit/Loss: {pnl:.2f} USD")
        
        content = "\n".join(message_parts)
        
        webhook_type = "volume_on" if is_volume_on else "volume_off"
        self._send_embed_notification(embed=None, webhook_type=webhook_type, content=content) # Pass content, embed is None

    def send_list_change_notification(self, added: set, removed: set, is_volume_on: bool = False, webhook_type: str = None, is_local_test: bool = False):
        """Formats and sends a notification for changes in the strong target list."""
        # Allow sending empty notifications if webhook_type is explicitly provided (e.g., for general_targets)
        # or if there are actual changes, or if in local test mode.
        if not added and not removed and not webhook_type and not is_local_test:
            return

        # AC4: Add identification to notification content
        strategy_tag = "[Volume ON]" if is_volume_on else "[Volume OFF]"
        
        message_parts = []
        if webhook_type == "general_targets":
            message_parts.append("General Strong Target List Update")
            if added:
                added_str = ", ".join(map(str, added))
                if len(added_str) > 1800: # Leave some room for other parts of the message
                    added_str = added_str[:1800] + "... (truncated)"
                message_parts.append(f"✅ All Targets: {added_str}")
            if removed:
                removed_str = ", ".join(map(str, removed))
                if len(removed_str) > 1800: # Leave some room for other parts of the message
                    removed_str = removed_str[:1800] + "... (truncated)"
                message_parts.append(f"❌ Removed: {removed_str}")
        else:
            message_parts.append(f"Strong Target List Updated {strategy_tag}")
            if added:
                added_str = ", ".join(map(str, added))
                message_parts.append(f"✅ Added: {added_str}")
            if removed:
                removed_str = ", ".join(map(str, removed))
                message_parts.append(f"❌ Removed: {removed_str}")
        
        content = "\n".join(message_parts)

        # Use provided webhook_type if available, otherwise default based on is_volume_on
        target_webhook_type = webhook_type if webhook_type else ("volume_on" if is_volume_on else "volume_off")
        self._send_embed_notification(embed=None, webhook_type=target_webhook_type, content=content)

    def send_subscription_notification(self, symbols: list, subscription_type: str):
        """Formats and sends a notification for k-line stream subscriptions."""
        if not symbols:
            return

        symbols_str = ", ".join(map(str, symbols))
        if len(symbols_str) > 1800:
            symbols_str = symbols_str[:1800] + "... (truncated)"
        
        message = f"Successfully subscribed to {subscription_type} k-line streams for: {symbols_str}"
        self._send_embed_notification(embed=None, webhook_type='general_targets', content=message)

    def send_heartbeat_notification(self, is_volume_on: bool = None, message: str = None):
        """Sends a heartbeat notification to indicate the bot is running."""
        content = message if message is not None else "Heartbeat: Bot is alive and running."
        if is_volume_on is None:
            # If no specific strategy is mentioned, send to both for a general heartbeat.
            self._send_embed_notification(embed=None, webhook_type='volume_on', content=content)
            self._send_embed_notification(embed=None, webhook_type='volume_off', content=content)
        else:
            # If a specific strategy is mentioned, send only to that one.
            webhook_type = "volume_on" if is_volume_on else "volume_off"
            self._send_embed_notification(embed=None, webhook_type=webhook_type, content=content)