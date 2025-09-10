import logging
from discord_webhook import DiscordWebhook, DiscordEmbed

class NotificationService:
    def __init__(self, webhook_url):
        if not webhook_url:
            # If no webhook is provided, log a warning and disable the service.
            logging.warning("Discord webhook URL is not set. NotificationService will be disabled.")
            self.webhook_url = None
        else:
            self.webhook_url = webhook_url

    def _send_embed_notification(self, embed: DiscordEmbed):
        """Sends a notification with a prepared DiscordEmbed object."""
        if not self.webhook_url:
            return # Do nothing if the service is disabled

        try:
            webhook = DiscordWebhook(url=self.webhook_url)
            webhook.add_embed(embed)
            response = webhook.execute()
            if response.status_code not in [200, 204]:
                logging.error(f"Discord webhook failed with status {response.status_code}: {response.content}")
        except Exception as e:
            logging.error(f"Error sending Discord notification: {e}", exc_info=True)

    def send_trade_notification(self, symbol, action, price, units, reason=None, pnl=None, stop_loss_price=None):
        """Formats and sends a trade event notification."""
        color = "00ff00" if action == "BUY" else "ff0000" # Green for BUY, Red for SELL
        title = f"Trade Executed: {action.upper()} {symbol}"
        
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
        
        self._send_embed_notification(embed)

    def send_list_change_notification(self, added: set, removed: set):
        """Formats and sends a notification for changes in the strong target list."""
        if not added and not removed:
            return

        title = "Strong Target List Updated"
        embed = DiscordEmbed(title=title, color="0000ff") # Blue for info

        if added:
            added_str = ", ".join(added)
            embed.add_embed_field(name="✅ Added", value=added_str, inline=False)
        
        if removed:
            removed_str = ", ".join(removed)
            embed.add_embed_field(name="❌ Removed", value=removed_str, inline=False)
            
        embed.set_timestamp()
        
        self._send_embed_notification(embed)
