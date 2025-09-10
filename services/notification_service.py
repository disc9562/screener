import logging
from discord_webhook import DiscordWebhook, DiscordEmbed

class NotificationService:
    def __init__(self, webhook_urls_map: dict):
        self.webhook_urls_map = webhook_urls_map

    def _send_embed_notification(self, embed: DiscordEmbed, webhook_type: str = 'default'):
        """Sends a notification with a prepared DiscordEmbed object to the specified webhook type."""
        webhook_url = self.webhook_urls_map.get(webhook_type)
        if not webhook_url:
            logging.warning(f"Discord webhook URL for type '{webhook_type}' is not set. Notification will not be sent.")
            return

        try:
            webhook = DiscordWebhook(url=webhook_url)
            webhook.add_embed(embed)
            response = webhook.execute()
            if response.status_code not in [200, 204]:
                logging.error(f"Discord webhook for type '{webhook_type}' failed with status {response.status_code}: {response.content}")
        except Exception as e:
            logging.error(f"Error sending Discord notification for type '{webhook_type}': {e}", exc_info=True)

    def send_trade_notification(self, symbol, action, price, units, reason=None, pnl=None, stop_loss_price=None, is_volume_on: bool = False):
        """Formats and sends a trade event notification."""
        color = "00ff00" if action == "BUY" else "ff0000" # Green for BUY, Red for SELL
        
        # AC4: Add identification to notification content
        strategy_tag = "[Volume ON]" if is_volume_on else "[Volume OFF]"
        title = f"Trade Executed {strategy_tag}: {action.upper()} {symbol}"
        
        embed = DiscordEmbed(title=title, color=color)
        embed.add_embed_field(name="Price", value=f"{price:.4f}")
        if action == "BUY" and stop_loss_price is not None:
            embed.add_embed_field(name="Stop Loss", value=f"{stop_loss_price:.4f}")
        embed.add_embed_field(name="Units", value=f"{units:.4f}")
        if reason:
            embed.add_embed_field(name="Reason", value=str(reason))
        if pnl is not None:
            embed.add_embed_field(name="Profit/Loss", value=f"{pnl:.2f} USD")
        embed.set_timestamp()
        
        webhook_type = "volume_on" if is_volume_on else "volume_off"
        self._send_embed_notification(embed, webhook_type=webhook_type)

    def send_list_change_notification(self, added: set, removed: set, is_volume_on: bool = False):
        """Formats and sends a notification for changes in the strong target list."""
        if not added and not removed:
            return

        # AC4: Add identification to notification content
        strategy_tag = "[Volume ON]" if is_volume_on else "[Volume OFF]"
        title = f"Strong Target List Updated {strategy_tag}"
        embed = DiscordEmbed(title=title, color="0000ff") # Blue for info

        if added:
            added_str = ", ".join(added)
            embed.add_embed_field(name="✅ Added", value=added_str, inline=False)
        
        if removed:
            removed_str = ", ".join(removed)
            embed.add_embed_field(name="❌ Removed", value=removed_str, inline=False)
            
        embed.set_timestamp()
        
        webhook_type = "volume_on" if is_volume_on else "volume_off" # Default to volume_off for list changes
        self._send_embed_notification(embed, webhook_type=webhook_type)
