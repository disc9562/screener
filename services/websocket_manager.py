import json
import logging
import queue
import threading
from binance.spot import Spot as SpotClient
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient

class WebSocketManager:
    def __init__(self, api_key, api_secret, notification_service):
        self.ws_client = None
        self.queue = queue.Queue()
        self.logger = logging.getLogger(__name__)
        self.api_key = api_key
        self.api_secret = api_secret
        self.notification_service = notification_service

    def _message_handler(self, _, message):
        """Callback function to handle incoming websocket messages."""
        try:
            data = json.loads(message)
            if 'e' in data and data['e'] == 'error':
                self.logger.error(f"WebSocket Error: {data.get('m')}")
            elif 'k' in data:
                self.queue.put(data) # Put the whole kline event
        except Exception as e:
            self.logger.error(f"Error processing websocket message: {e}")

    def start(self, symbols: list, interval='15m'):
        """Starts the WebSocket client and subscribes to streams."""
        if self.ws_client:
            self.logger.warning("WebSocket client already running.")
            return

        self.logger.info("Starting WebSocketManager...")
        self.ws_client = SpotWebsocketStreamClient(on_message=self._message_handler)
        
        if not symbols:
            self.logger.warning("No symbols provided to subscribe.")
            return

        # Binance API expects streams in lowercase
        streams = [f"{symbol.lower()}@kline_{interval}" for symbol in symbols]
        self.logger.info(f"Subscribing to streams: {streams}")
        self.ws_client.subscribe(stream=streams)
        self.notification_service.send_subscription_notification(symbols, "initial")

    def subscribe(self, symbols: list, interval='15m'):
        """Subscribes to additional streams."""
        if not self.ws_client:
            self.logger.warning("WebSocket client not started. Cannot subscribe to new symbols.")
            return
        
        if not symbols:
            self.logger.warning("No symbols provided to subscribe.")
            return

        streams = [f"{symbol.lower()}@kline_{interval}" for symbol in symbols]
        self.logger.info(f"Subscribing to additional streams: {streams}")
        self.ws_client.subscribe(stream=streams)
        self.notification_service.send_subscription_notification(symbols, "additional")

    def get_message(self, block=True, timeout=None):
        """Gets a message from the queue."""
        try:
            return self.queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        """Stops the WebSocket client."""
        if self.ws_client:
            self.logger.info("Stopping WebSocketManager...")
            self.ws_client.stop()
            self.ws_client = None