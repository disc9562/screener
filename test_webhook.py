import logging
import os
from dotenv import load_dotenv
from discord_webhook import DiscordWebhook, DiscordEmbed


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_GENERAL_TARGETS", "")
    if not webhook_url:
        logging.error("DISCORD_WEBHOOK_URL_GENERAL_TARGETS is not set in environment.")
        return

    try:
        webhook = DiscordWebhook(url=webhook_url)
        embed = DiscordEmbed(title="Test Embed", description="This is a test embed from a script.", color="00ff00")
        webhook.add_embed(embed)

        response = webhook.execute()

        if response.status_code in [200, 204]:
            logging.info(f"Successfully sent webhook. Status: {response.status_code}")
        else:
            logging.error(f"Webhook failed with status {response.status_code}: {response.content}")

    except Exception as e:
        logging.error(f"Error sending webhook: {e}", exc_info=True)


if __name__ == "__main__":
    main()