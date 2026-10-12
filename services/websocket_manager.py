import json
import logging
import queue
import threading
import time
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from websocket._exceptions import WebSocketConnectionClosedException

class WebSocketManager:
    def __init__(self, api_key, api_secret, notification_service):
        self.api_key = api_key
        self.api_secret = api_secret
        self.notification_service = notification_service
        self.queue = queue.Queue()
        self.logger = logging.getLogger(__name__)
        self.is_running = False
        self.thread = None
        self.ws_client = None
        self.symbols = []
        self.interval = '15m'
        self.initial_notification_sent = False # Flag to track initial notification

    def _message_handler(self, _, message):
        """Callback function to handle incoming websocket messages."""
        try:
            data = json.loads(message)
            # Check for errors first
            if 'e' in data and data['e'] == 'error':
                self.logger.error(f"WebSocket API Error: {data.get('m')}")
                return

            # Only queue the message if it's a closed k-line
            if data.get('k', {}).get('x'):
                kline_data = data['k']
                # Keep kline in dict format for use in main.py and alligator_strategy.py
                self.logger.info(f"Queuing closed k-line for {data.get('s')}")
                self.queue.put({'k': kline_data, 's': data.get('s')}) # Put kline dict and symbol

        except Exception as e:
            self.logger.error(f"Error processing websocket message: {e}")

    def _run(self):
        """The main loop for the WebSocket client with reconnection logic."""
        self.logger.info("WebSocketManager thread started.")
        while self.is_running:
            try:
                self.logger.info(f"Attempting to connect and subscribe to {len(self.symbols)} symbols...")
                self.ws_client = SpotWebsocketStreamClient(on_message=self._message_handler, on_close=self._on_close, on_error=self._on_error)
                
                if not self.symbols:
                    self.logger.warning("No symbols to subscribe to. Waiting...")
                    time.sleep(10) # Wait before retrying if no symbols
                    continue

                streams = [f"{symbol.lower()}@kline_{self.interval}" for symbol in self.symbols]
                self.ws_client.subscribe(stream=streams)
                
                # Only send notification on the very first successful subscription
                if not self.initial_notification_sent:
                    self.notification_service.send_subscription_notification(self.symbols, "initial")
                    self.initial_notification_sent = True

                self.logger.info(f"Successfully subscribed to streams. Waiting for messages.")
                
                while self.is_running:
                    time.sleep(1)

            except WebSocketConnectionClosedException:
                self.logger.warning("WebSocket connection lost. Attempting to reconnect in 10 seconds...")
            except Exception as e:
                self.logger.error(f"An unexpected error occurred in WebSocketManager: {e}. Retrying in 30 seconds...")
                time.sleep(15) # Longer sleep for unexpected errors
            finally:
                if self.ws_client:
                    self.ws_client.stop()
                if self.is_running:
                    time.sleep(10) # Wait before attempting to reconnect
        self.logger.info("WebSocketManager thread stopped.")

    def _on_close(self, _):
        self.logger.warning("WebSocket connection closed.")

    def _on_error(self, _, error):
        self.logger.error(f"WebSocket error: {error}")

    def start(self, symbols: list, interval='15m'):
        """Starts the WebSocket manager thread."""
        if self.is_running:
            self.logger.warning("WebSocketManager is already running.")
            return

        self.is_running = True
        self.symbols = list(set(symbols)) # Store unique symbols
        self.interval = interval
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.logger.info("WebSocketManager started.")

    def subscribe(self, symbols: list):
        """Subscribes to additional symbols."""
        if not self.is_running or not self.ws_client:
            self.logger.warning("WebSocket client not started. Cannot subscribe.")
            return

        new_symbols = [s for s in symbols if s not in self.symbols]
        if not new_symbols:
            self.logger.info("No new symbols to subscribe to.")
            return

        self.symbols.extend(new_symbols)
        streams = [f"{symbol.lower()}@kline_{self.interval}" for symbol in new_symbols]
        self.logger.info(f"Subscribing to additional streams: {streams}")
        try:
            self.ws_client.subscribe(stream=streams)
            self.notification_service.send_subscription_notification(new_symbols, "additional")
        except WebSocketConnectionClosedException:
            self.logger.error("Failed to subscribe to new symbols because the WebSocket connection is closed. The manager will attempt to reconnect automatically.")
        except Exception as e:
            self.logger.error(f"An unexpected error occurred during subscription: {e}")

    def get_message(self, block=True, timeout=None):
        """Gets a message from the queue."""
        try:
            return self.queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        """Stops the WebSocket manager thread."""
        if not self.is_running:
            self.logger.info("WebSocketManager is not running.")
            return

        self.logger.info("Stopping WebSocketManager...")
        self.is_running = False
        if self.ws_client:
            self.ws_client.stop() # Gracefully stop the client
        if self.thread:
            self.thread.join(timeout=5) # Wait for the thread to finish
        self.logger.info("WebSocketManager stopped.")