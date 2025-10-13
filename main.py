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
from data.fetcher import CryptoFetcher
from strategy.strong_target_screener import StrongTargetScreener
from strategy.alligator_strategy import AlligatorStrategy
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='application.log', filemode='w')

def process_kline_message(symbol: str, kline: dict, position_manager: PositionManager, alligator_strategy: AlligatorStrategy, strong_targets: set, is_volume_on: bool):
    """Processes a single kline for a given strategy in a stateful manner."""
    try:
        # 1. Update position status with the latest price from the kline
        # Note: The kline from websocket is a list, but update_positions expects a dict-like object.
        # We create a small DataFrame for compatibility.
        latest_price_df = pd.DataFrame([{
            'Datetime': pd.to_datetime(kline['t'], unit='ms'),
            'High': float(kline['h']),
            'Low': float(kline['l']),
            'Close': float(kline['c'])
        }])
        position_manager.update_positions({symbol: latest_price_df.iloc[0]})

        # 2. If the symbol is a strong target, run the stateful strategy
        if symbol in strong_targets:
            signal = alligator_strategy.run_with_kline(symbol, kline)
            
            # 3. If a signal is generated, attempt to open a position
            if signal:
                if symbol not in position_manager.get_open_positions_symbols():
                    logging.info(f"SIGNALS FOUND for {symbol} (Volume ON: {is_volume_on}): {signal}")
                    position_manager.open_position(symbol, signal)
                else:
                    logging.info(f"Position for {symbol} is already open. Ignoring new BUY signal.")

    except Exception as e:
        logging.error(f"Error in process_kline_message for {symbol}: {e}", exc_info=True)

def run_app(args):
    logging.info("Application starting...")

    strategy_config = get_strategy_config()
    webhook_on = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_ON")
    webhook_off = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_OFF")
    if not webhook_on or not webhook_off:
        logging.error("Error: Both DISCORD_WEBHOOK_URL_VOLUME_ON and DISCORD_WEBHOOK_URL_VOLUME_OFF must be configured.")
        sys.exit(1)

    notification_service = NotificationService(webhook_urls_map={
        "volume_on": webhook_on,
        "volume_off": webhook_off,
        "general_targets": strategy_config.get("DISCORD_WEBHOOK_URL_GENERAL_TARGETS")
    })
    
    timeout = args.timeout

    # Instantiate two strategy/position manager pairs
    strategy_config_volume_on = {**strategy_config, "use_volume_condition": True}
    position_manager_volume_on = PositionManager(notification_service, strategy_config_volume_on, 'data/positions_volume_on.csv')
    alligator_strategy_volume_on = AlligatorStrategy(strategy_config_volume_on)

    strategy_config_volume_off = {**strategy_config, "use_volume_condition": False}
    position_manager_volume_off = PositionManager(notification_service, strategy_config_volume_off, 'data/positions_volume_off.csv')
    alligator_strategy_volume_off = AlligatorStrategy(strategy_config_volume_off)

    strategy_pairs = {
        "volume_on": {"position_manager": position_manager_volume_on, "alligator_strategy": alligator_strategy_volume_on, "strong_targets": set(), "is_volume_on": True},
        "volume_off": {"position_manager": position_manager_volume_off, "alligator_strategy": alligator_strategy_volume_off, "strong_targets": set(), "is_volume_on": False}
    }
    
    screener = StrongTargetScreener({**strategy_config, 'local_test_mode': getattr(args, 'local_test', False)})

    # Fetch initial strong targets
    logging.info("Performing initial screening for strong targets...")
    strong_targets_with_scores = screener.run_screener()
    top_20_targets = {item[0] for item in strong_targets_with_scores}

    # --- Warmup Phase ---
    logging.info("Starting warmup phase for strategies...")
    crypto_fetcher = CryptoFetcher()
    for symbol in top_20_targets:
        try:
            historical_klines = crypto_fetcher.fetch_klines(symbol, '15m', limit=250)
            if historical_klines:
                historical_df = transform_crypto_data(historical_klines)
                if not historical_df.empty:
                    # Warm up both strategies with the same historical data
                    alligator_strategy_volume_on.warmup(symbol, historical_df)
                    alligator_strategy_volume_off.warmup(symbol, historical_df)
                else:
                    logging.warning(f"Could not generate DataFrame for {symbol} during warmup.")
            else:
                logging.warning(f"No historical k-lines found for {symbol} during warmup.")
        except Exception as e:
            logging.error(f"Failed to fetch/warmup for {symbol}: {e}", exc_info=True)

    # Assign targets and notify
    for pair in strategy_pairs.values():
        pair["strong_targets"] = top_20_targets
        notification_service.send_list_change_notification(added=top_20_targets, removed=set(), is_volume_on=pair["is_volume_on"], is_local_test=getattr(args, 'local_test', False))
    notification_service.send_list_change_notification(added=top_20_targets, removed=set(), webhook_type="general_targets", is_local_test=getattr(args, 'local_test', False))

    symbols_to_subscribe = list(top_20_targets.union(position_manager_volume_on.get_open_positions_symbols(), position_manager_volume_off.get_open_positions_symbols()))

    # --- Main Processing Loop ---
    if getattr(args, 'local_test', False):
        logging.warning("Local test mode is not supported with the new stateful architecture. Exiting.")
        return

    ws_manager = WebSocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET, notification_service=notification_service)
    ws_manager.start(symbols=symbols_to_subscribe, interval='15m')

    logging.info("Entering main processing loop...")
    last_heartbeat_minute = -1

    try:
        while True:
            current_minute = datetime.now().minute
            if current_minute % 15 == 0 and current_minute != last_heartbeat_minute:
                if not position_manager_volume_on.get_open_positions_symbols() and not position_manager_volume_off.get_open_positions_symbols():
                    logging.info("HEARTBEAT_CHECK: No open positions. Sending notification.")
                    notification_service.send_heartbeat_notification(message="沒有艙位")
                last_heartbeat_minute = current_minute

            try:
                message = ws_manager.get_message(block=True, timeout=1)
                if message:
                    kline = message['k']
                    symbol = message['s']
                    
                    # Process the kline for each strategy pair
                    for pair in strategy_pairs.values():
                        process_kline_message(
                            symbol,
                            kline,
                            pair["position_manager"],
                            pair["alligator_strategy"],
                            pair["strong_targets"],
                            pair["is_volume_on"]
                        )
            except queue.Empty:
                continue

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        logging.info("Stopping WebSocket Manager...")
        ws_manager.stop()
        logging.info("Application stopped.")

def main():
    parser = argparse.ArgumentParser(description="Crypto Screener Bot")
    parser.add_argument('--timeout', type=int, help='Timeout in seconds for debug mode.')
    parser.add_argument('--fetch-now', action='store_true', help='Immediately fetch strong targets, bypassing schedule.')
    parser.add_argument('--local-test', action='store_true', help='Enable local test mode (e.g., subset of coins, no WebSocket).')
    args = parser.parse_args()
    run_app(args)

if __name__ == "__main__":
    main()