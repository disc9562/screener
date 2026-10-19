import json
import logging
import queue
import threading
import time
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from websocket._exceptions import WebSocketConnectionClosedException

# USDⓈ-M 合約 stream 已拆成 /public、/market、/private；kline 在 /market（沒帶路徑的連線只收得到 public，
# 訂閱會回成功但永遠沒有 kline）。現貨 client 指到這個網址，訊息格式相同。
FUTURES_STREAM_URL = "wss://fstream.binance.com/market"
# 合約 kline 每 ~250ms 推一次未收完的 K 棒；超過這麼久沒有任何訊息就視為連線已死（含 Binance 每 24h 強制斷線）
STALE_SECONDS = 90

class WebSocketManager:
    def __init__(self, api_key, api_secret, notification_service, stream_url=FUTURES_STREAM_URL):
        self.stream_url = stream_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.notification_service = notification_service
        self.queue = queue.Queue()
        self.logger = logging.getLogger(__name__)
        self.is_running = False
        self.thread = None
        self.ws_client = None
        self.symbols = []
        self._lock = threading.Lock()
        self.interval = '15m'
        self.initial_notification_sent = False # Flag to track initial notification
        self.last_message_at = 0.0             # time.monotonic() of the last message of any kind

    def _message_handler(self, _, message):
        """Callback function to handle incoming websocket messages."""
        self.last_message_at = time.monotonic()
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
                with self._lock:
                    current_symbols = list(self.symbols)
                self.logger.info(f"Attempting to connect and subscribe to {len(current_symbols)} symbols...")
                self.ws_client = SpotWebsocketStreamClient(stream_url=self.stream_url, on_message=self._message_handler,
                                                           on_close=self._on_close, on_error=self._on_error)
                
                if not current_symbols:
                    self.logger.warning("No symbols to subscribe to. Waiting...")
                    time.sleep(10) # Wait before retrying if no symbols
                    continue

                streams = [f"{symbol.lower()}@kline_{self.interval}" for symbol in current_symbols]
                self.ws_client.subscribe(stream=streams)
                
                # Only send notification on the very first successful subscription
                if not self.initial_notification_sent:
                    self.notification_service.send_subscription_notification(current_symbols, "initial")
                    self.initial_notification_sent = True

                self.logger.info(f"Successfully subscribed to streams. Waiting for messages.")
                self.last_message_at = time.monotonic()

                # binance-connector 的接收執行緒遇到任何例外就結束；這裡要主動偵測並重連
                while self.is_running and self._healthy():
                    time.sleep(1)
                if self.is_running:
                    self.logger.warning(f"WebSocket dead or silent > {STALE_SECONDS}s. Reconnecting...")

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

    def _healthy(self):
        sm = getattr(self.ws_client, "socket_manager", None)
        if sm is not None and not sm.is_alive():
            return False
        return time.monotonic() - self.last_message_at <= STALE_SECONDS

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
        with self._lock:
            self.symbols = list(set(symbols)) # Store unique symbols
        self.interval = interval
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.logger.info("WebSocketManager started.")

    def subscribe(self, symbols: list):
        """Subscribes to additional symbols."""
        if not self.is_running:
            self.logger.warning("WebSocket client not started. Cannot subscribe.")
            return

        with self._lock:
            new_symbols = [s for s in symbols if s not in self.symbols]
            if not new_symbols:
                self.logger.info("No new symbols to subscribe to.")
                return
            self.symbols.extend(new_symbols)

        streams = [f"{symbol.lower()}@kline_{self.interval}" for symbol in new_symbols]
        self.logger.info(f"Subscribing to additional streams: {streams}")
        if not self.ws_client:                 # 尚未連上：已加入 self.symbols，連上時會一併訂閱
            return
        try:
            self.ws_client.subscribe(stream=streams)
            self.notification_service.send_subscription_notification(new_symbols, "additional")
        except WebSocketConnectionClosedException:
            self.logger.error("Failed to subscribe to new symbols because the WebSocket connection is closed. The manager will attempt to reconnect automatically.")
        except Exception as e:
            self.logger.error(f"An unexpected error occurred during subscription: {e}")

    def unsubscribe(self, symbols: list):
        """Unsubscribes from symbols that are no longer needed."""
        if not self.is_running:
            return
        with self._lock:
            removed = [s for s in symbols if s in self.symbols]
            if not removed:
                return
            self.symbols = [s for s in self.symbols if s not in set(removed)]
        if not self.ws_client:
            return
        streams = [f"{symbol.lower()}@kline_{self.interval}" for symbol in removed]
        self.logger.info(f"Unsubscribing from streams: {streams}")
        try:
            if hasattr(self.ws_client, "unsubscribe"):
                self.ws_client.unsubscribe(stream=streams)
        except Exception as e:
            self.logger.warning(f"Failed to unsubscribe streams {streams}: {e}")

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