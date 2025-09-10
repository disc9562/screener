import os
import sys
import time
import logging
import queue
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from config import get_strategy_config, BINANCE_API_KEY, BINANCE_API_SECRET, DISCORD_WEBHOOK_URL
from data.transformer import transform_crypto_data
from strategy.strong_target_screener import StrongTargetScreener
from strategy.alligator_strategy import AlligatorStrategy
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

kline_cache = {}

def process_kline_message(msg, position_manager, alligator_strategy, strong_targets):
    try:
        if msg.get('k', {}).get('x'): # Process only closed klines
            kline_data = msg['k']
            symbol = kline_data['s']
            
            if symbol not in kline_cache:
                kline_cache[symbol] = []
            kline_cache[symbol].append(kline_data)
            if len(kline_cache[symbol]) > 500:
                kline_cache[symbol].pop(0)

            df = transform_crypto_data(kline_cache[symbol])
            if df.empty:
                return

            position_manager.update_positions({symbol: df.iloc[-1]})
            
            if symbol in strong_targets:
                signals = alligator_strategy.run_with_existing_data(df)
                if signals:
                    last_signal = signals[-1]
                    logging.info(f"SIGNALS FOUND for {symbol}: {last_signal}")
                    position_manager.open_position(symbol, last_signal)

    except Exception as e:
        logging.error(f"Error processing kline message: {e}", exc_info=True)

def main(args):
    logging.info("Application starting...")
    
    timeout = args.timeout

    strategy_config = get_strategy_config()
    notification_service = NotificationService(webhook_url=DISCORD_WEBHOOK_URL)
    position_manager = PositionManager(notification_service=notification_service, strategy_config=strategy_config)
    alligator_strategy = AlligatorStrategy(strategy_config)
    
    logging.info("Performing initial screening for strong targets...")
    screener = StrongTargetScreener(strategy_config)
    strong_targets = set(screener.run_screener())
    logging.info(f"Initial strong targets: {strong_targets}")
    notification_service.send_list_change_notification(added=strong_targets, removed=set())

    open_positions = set(position_manager.get_open_positions_symbols())
    symbols_to_subscribe = list(strong_targets.union(open_positions))

    ws_manager = WebSocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
    ws_manager.start(symbols=symbols_to_subscribe, interval='15m')

    logging.info("Entering main processing loop...")
    last_screener_run = datetime.now()
    start_time = datetime.now()

    try:
        while True:
            if timeout and (datetime.now() - start_time) > timedelta(seconds=timeout):
                logging.info(f"Timeout of {timeout} seconds reached. Exiting.")
                break

            if datetime.now() - last_screener_run > timedelta(hours=4):
                logging.info("Periodically re-running screener...")
                new_strong_targets = set(screener.run_screener())
                
                current_symbols = set(symbols_to_subscribe)
                added = new_strong_targets - current_symbols
                removed = current_symbols - new_strong_targets - open_positions
                
                if added:
                    logging.info(f"New symbols to subscribe to: {added}")
                    ws_manager.subscribe(list(added))
                    symbols_to_subscribe.extend(list(added))
                
                if added or removed:
                    notification_service.send_list_change_notification(added=added, removed=removed)
                
                strong_targets = new_strong_targets
                last_screener_run = datetime.now()

            try:
                message = ws_manager.get_message(block=True, timeout=1)
                if message:
                    process_kline_message(message, position_manager, alligator_strategy, strong_targets)
            except queue.Empty:
                continue

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        logging.info("Stopping WebSocket Manager...")
        ws_manager.stop()
        logging.info("Application stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto Screener Bot")
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    args = parser.parse_args()
    main(args)
