import os
import sys
import time
import logging
import queue
import argparse
from datetime import datetime, timedelta
from unittest.mock import MagicMock # Added for local test mode

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from config import get_strategy_config, BINANCE_API_KEY, BINANCE_API_SECRET
from data.transformer import transform_crypto_data
from strategy.strong_target_screener import StrongTargetScreener
from strategy.alligator_strategy import AlligatorStrategy
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

kline_cache = {}

def process_kline_message(msg, position_manager, alligator_strategy, strong_targets, is_volume_on: bool):
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
                # Pass is_volume_on to run_with_existing_data if it needs to be used there
                # For now, AlligatorStrategy uses its own config, so no need to pass here
                signals = alligator_strategy.run_with_existing_data(df)
                if signals:
                    last_signal = signals[-1]
                    logging.info(f"SIGNALS FOUND for {symbol} (Volume ON: {is_volume_on}): {last_signal}")
                    position_manager.open_position(symbol, last_signal, is_volume_on=is_volume_on) # Pass is_volume_on

    except Exception as e:
        logging.error(f"Error processing kline message: {e}", exc_info=True)

def main(args):
    logging.info("Application starting...")
    
    timeout = args.timeout

    strategy_config = get_strategy_config()

    # AC5: Strict Webhook Configuration Validation
    webhook_on = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_ON")
    webhook_off = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_OFF")

    if not webhook_on or not webhook_off:
        logging.error("Error: Both DISCORD_WEBHOOK_URL_VOLUME_ON and DISCORD_WEBHOOK_URL_VOLUME_OFF must be configured in .env")
        sys.exit(1) # Exit the application

    webhook_urls_map = {
        "volume_on": webhook_on,
        "volume_off": webhook_off,
        "general_targets": strategy_config.get("DISCORD_WEBHOOK_URL_GENERAL_TARGETS")
    }
    notification_service = NotificationService(webhook_urls_map=webhook_urls_map)

    # Task 5.1: Instantiate two strategy/position manager pairs
    # Strategy 1: Volume ON
    strategy_config_volume_on = strategy_config.copy()
    strategy_config_volume_on["use_volume_condition"] = True
    position_manager_volume_on = PositionManager(notification_service=notification_service, strategy_config=strategy_config_volume_on)
    alligator_strategy_volume_on = AlligatorStrategy(strategy_config_volume_on)

    # Strategy 2: Volume OFF
    strategy_config_volume_off = strategy_config.copy()
    strategy_config_volume_off["use_volume_condition"] = False
    position_manager_volume_off = PositionManager(notification_service=notification_service, strategy_config=strategy_config_volume_off)
    alligator_strategy_volume_off = AlligatorStrategy(strategy_config_volume_off)

    # Store strategy pairs in a dictionary for easier iteration
    strategy_pairs = {
        "volume_on": {
            "position_manager": position_manager_volume_on,
            "alligator_strategy": alligator_strategy_volume_on,
            "strong_targets": set(), # Initial empty set for strong targets
            "is_volume_on": True
        },
        "volume_off": {
            "position_manager": position_manager_volume_off,
            "alligator_strategy": alligator_strategy_volume_off,
            "strong_targets": set(), # Initial empty set for strong targets
            "is_volume_on": False
        }
    }
    
    screener = StrongTargetScreener(strategy_config) # Screener uses base config
    screener.config['local_test_mode'] = getattr(args, 'local_test', False) # Pass local_test_mode to screener

    # Task 3.2: Implement --fetch-now logic
    all_symbols_to_subscribe = set() # Initialize here to ensure it's always defined
    if getattr(args, 'fetch_now', False):
        logging.info("Manual fetch triggered (--fetch-now). Bypassing schedule.")
        for strategy_type, pair in strategy_pairs.items():
            initial_strong_targets = set(screener.run_screener())
            pair["strong_targets"] = initial_strong_targets
            logging.info(f"Initial strong targets for {strategy_type} strategy: {initial_strong_targets}")
            notification_service.send_list_change_notification(
                added=initial_strong_targets, removed=set(), is_volume_on=pair["is_volume_on"], is_local_test=getattr(args, 'local_test', False)
            )
            all_symbols_to_subscribe.update(initial_strong_targets) # Move inside loop
            all_symbols_to_subscribe.update(pair["position_manager"].get_open_positions_symbols()) # Move inside loop
    else:
        logging.info("Performing initial screening for strong targets (scheduled).")
        # Run initial screening for both strategies
        for strategy_type, pair in strategy_pairs.items():
            initial_strong_targets = set(screener.run_screener())
            pair["strong_targets"] = initial_strong_targets
            logging.info(f"Initial strong targets for {strategy_type} strategy: {initial_strong_targets}")
            notification_service.send_list_change_notification(
                added=initial_strong_targets, removed=set(), is_volume_on=pair["is_volume_on"], is_local_test=getattr(args, 'local_test', False)
            )
            all_symbols_to_subscribe.update(initial_strong_targets) # Move inside loop
            all_symbols_to_subscribe.update(pair["position_manager"].get_open_positions_symbols()) # Move inside loop

    symbols_to_subscribe = list(all_symbols_to_subscribe)

    # Task 3.1: Disable WebSocket connection in local test mode
    if not getattr(args, 'local_test', False):
        ws_manager = WebSocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
        ws_manager.start(symbols=symbols_to_subscribe, interval='15m')
    else:
        logging.info("WebSocket connection disabled in local test mode.")
        ws_manager = MagicMock() # Mock ws_manager if not initialized

    logging.info("Entering main processing loop...")
    last_screener_run = datetime.now()
    start_time = datetime.now()
    fetched_times_today = set() # To track scheduled fetches for today

    try:
        while True:
            
            if timeout and (datetime.now() - start_time) > timedelta(seconds=timeout):
                logging.info(f"Timeout of {timeout} seconds reached. Exiting.")
                break

            # Task 2.2: Implement scheduled fetching logic
            current_time = datetime.now()
            today_str = current_time.strftime("%Y-%m-%d")

            for fetch_time_str in strategy_config.get("TARGET_FETCH_TIMES"):
                fetch_hour, fetch_minute = map(int, fetch_time_str.split(':'))
                scheduled_fetch_time = current_time.replace(hour=fetch_hour, minute=fetch_minute, second=0, microsecond=0)

                # Check if it's time to fetch and if it hasn't been fetched today for this scheduled time
                
                if (current_time >= scheduled_fetch_time and
                    (today_str, fetch_time_str) not in fetched_times_today):
                    
                    logging.info(f"Scheduled re-running screener for {fetch_time_str}...")
                    for strategy_type, pair in strategy_pairs.items():
                        new_strong_targets = set(screener.run_screener())
                        
                        current_symbols = pair["strong_targets"]
                        added = new_strong_targets - current_symbols
                        removed = current_symbols - new_strong_targets - set(pair["position_manager"].get_open_positions_symbols())
                        
                        if added:
                            logging.info(f"New symbols to subscribe to for {strategy_type} strategy: {added}")
                            if not getattr(args, 'local_test', False):
                                    ws_manager.subscribe(list(added))
                            symbols_to_subscribe.extend(list(added))
                        
                        if added or removed:
                            notification_service.send_list_change_notification(
                                added=added, removed=removed, is_volume_on=pair["is_volume_on"], is_local_test=getattr(args, 'local_test', False)
                            )
                    # Send general strong targets notification
                    notification_service.send_list_change_notification(
                        added=all_symbols_to_subscribe, removed=set(), webhook_type="general_targets", is_local_test=getattr(args, 'local_test', False)
                    )
                    
                    pair["strong_targets"] = new_strong_targets
                    fetched_times_today.add((today_str, fetch_time_str)) # Mark as fetched for today

            if not getattr(args, 'local_test', False): # Only process WebSocket messages if not in local test mode
                try:
                    message = ws_manager.get_message(block=True, timeout=1)
                    if message:
                        # Process kline message for each strategy
                        for strategy_type, pair in strategy_pairs.items():
                            process_kline_message(
                                message,
                                pair["position_manager"],
                                pair["alligator_strategy"],
                                pair["strong_targets"],
                                pair["is_volume_on"] # Pass is_volume_on to process_kline_message
                            )
                except queue.Empty:
                    continue

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        if not getattr(args, 'local_test', False): # Only stop WebSocket Manager if not in local test mode
            logging.info("Stopping WebSocket Manager...")
            ws_manager.stop()
        logging.info("Application stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto Screener Bot")
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')
    parser.add_argument('--local-test', action='store_true', help='Enable local test mode (e.g., subset of coins, no WebSocket).')
    args = parser.parse_args()
    main(args)
