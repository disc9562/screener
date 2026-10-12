from discord_webhook import DiscordWebhook, DiscordEmbed
import logging

logging.basicConfig(level=logging.INFO)

# 請將這裡替換為您的 DISCORD_WEBHOOK_URL_GENERAL_TARGETS
WEBHOOK_URL = "https://discord.com/api/webhooks/1127790220692172890/4Bj8RrqT8lruyIU05FYNfmPDt-ji35zyyYT84zfgsEQhLm0lySAV4ex0vtzlsHnReZwQ"

try:
    webhook = DiscordWebhook(url=WEBHOOK_URL)
    embed = DiscordEmbed(title="Test Embed", description="This is a test embed from a script.", color="00ff00")
    webhook.add_embed(embed)
    
    response = webhook.execute()

    if response.status_code in [200, 204]:
        logging.info(f"Successfully sent webhook. Status: {response.status_code}")
    else:
        logging.error(f"Webhook failed with status {response.status_code}: {response.content}")

except Exception as e:
    logging.error(f"Error sending webhook: {e}", exc_info=True)