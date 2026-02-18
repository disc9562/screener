import os
import sys
import time
import logging
import queue
import argparse
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from config import (
    get_strategy_config, BINANCE_API_KEY, BINANCE_API_SECRET,
    USE_TESTNET, BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET,
)
from data.transformer import transform_crypto_data
from data.fetcher import CryptoFetcher
from strategy.strong_target_screener import StrongTargetScreener
from strategy.alligator_strategy import AlligatorStrategy
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService
from services.order_execution_service import OrderExecutionService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='application.log', filemode='w')


def process_kline_message(symbol: str, kline: dict, position_manager: PositionManager,
                          alligator_strategy: AlligatorStrategy, strong_targets: set,
                          is_volume_on: bool):
    """Processes a single kline for a given strategy in a stateful manner."""
    try:
        latest_price = pd.Series({
            'Datetime': pd.to_datetime(kline['t'], unit='ms'),
            'High': float(kline['h']),
            'Low': float(kline['l']),
            'Close': float(kline['c']),
        })
        position_manager.update_positions({symbol: latest_price})

        if symbol not in strong_targets:
            return

        signal = alligator_strategy.run_with_kline(symbol, kline)
        if signal and symbol not in position_manager.get_open_positions_symbols():
            logging.info(f"SIGNALS FOUND for {symbol} (Volume ON: {is_volume_on}): {signal}")
            position_manager.open_position(symbol, signal)

    except Exception as e:
        logging.error(f"Error in process_kline_message for {symbol}: {e}", exc_info=True)


def _init_order_execution_service():
    """Initialize testnet order execution if enabled and configured."""
    if not USE_TESTNET:
        logging.info("Testnet order execution DISABLED (paper trading mode)")
        return None
    if not BINANCE_TESTNET_API_KEY or not BINANCE_TESTNET_API_SECRET:
        logging.warning("USE_TESTNET is enabled but BINANCE_TESTNET_API_KEY/SECRET not set. Running without order execution.")
        return None
    try:
        return OrderExecutionService(BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET)
    except Exception as e:
        logging.error(f"Failed to initialize OrderExecutionService: {e}")
        logging.warning("Continuing without testnet order execution.")
        return None


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
        "general_targets": strategy_config.get("DISCORD_WEBHOOK_URL_GENERAL_TARGETS"),
    })

    order_execution_service = _init_order_execution_service()

    # Instantiate two strategy/position manager pairs
    strategy_config_volume_on = {**strategy_config, "use_volume_condition": True}
    position_manager_volume_on = PositionManager(notification_service, strategy_config_volume_on, 'data/positions_volume_on.csv', order_execution_service=order_execution_service)
    alligator_strategy_volume_on = AlligatorStrategy(strategy_config_volume_on)

    strategy_config_volume_off = {**strategy_config, "use_volume_condition": False}
    position_manager_volume_off = PositionManager(notification_service, strategy_config_volume_off, 'data/positions_volume_off.csv', order_execution_service=order_execution_service)
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

    logging.info("Starting warmup phase for strategies...")
    crypto_fetcher = CryptoFetcher()
    for symbol in top_20_targets:
        try:
            historical_klines = crypto_fetcher.fetch_klines(symbol, '15m', limit=250)
            if not historical_klines:
                logging.warning(f"No historical k-lines found for {symbol} during warmup.")
                continue
            historical_df = transform_crypto_data(historical_klines)
            if historical_df.empty:
                logging.warning(f"Could not generate DataFrame for {symbol} during warmup.")
                continue
            for pair in strategy_pairs.values():
                pair["alligator_strategy"].warmup(symbol, historical_df)
        except Exception as e:
            logging.error(f"Failed to fetch/warmup for {symbol}: {e}", exc_info=True)

    # Assign targets and notify
    for pair in strategy_pairs.values():
        pair["strong_targets"] = top_20_targets
        notification_service.send_list_change_notification(added=top_20_targets, removed=set(), is_volume_on=pair["is_volume_on"], is_local_test=getattr(args, 'local_test', False))
    notification_service.send_list_change_notification(added=top_20_targets, removed=set(), webhook_type="general_targets", is_local_test=getattr(args, 'local_test', False))

    open_symbols_on = set(position_manager_volume_on.get_open_positions_symbols())
    open_symbols_off = set(position_manager_volume_off.get_open_positions_symbols())
    symbols_to_subscribe = list(top_20_targets | open_symbols_on | open_symbols_off)

    if getattr(args, 'local_test', False):
        logging.warning("Local test mode is not supported with the new stateful architecture. Exiting.")
        return

    ws_manager = WebSocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET, notification_service=notification_service)
    ws_manager.start(symbols=symbols_to_subscribe, interval='15m')

    logging.info("Entering main processing loop...")
    last_heartbeat_minute = -1
    fetched_times_today = set()
    today_str = datetime.now().strftime("%Y-%m-%d")

    try:
        while True:
            now = datetime.now()
            # Reset fetched times at the start of a new day
            current_day_str = now.strftime("%Y-%m-%d")
            if current_day_str != today_str:
                fetched_times_today = set()
                today_str = current_day_str
                logging.info("New day, resetting scheduled fetch times.")

            # Scheduled re-screening logic
            for fetch_time_str in strategy_config.get("TARGET_FETCH_TIMES", []):
                fetch_hour, fetch_minute = map(int, fetch_time_str.split(':'))
                if now.hour == fetch_hour and now.minute == fetch_minute and (today_str, fetch_time_str) not in fetched_times_today:
                    logging.info(f"Scheduled re-running screener for {fetch_time_str}...")
                    strong_targets_with_scores = screener.run_screener()
                    new_top_20_targets = {item[0] for item in strong_targets_with_scores}

                    for pair in strategy_pairs.values():
                        current_symbols = pair["strong_targets"]
                        added = new_top_20_targets - current_symbols
                        removed = current_symbols - new_top_20_targets
                        
                        if added:
                            logging.info(f"New symbols to subscribe to for strategy: {added}")
                            ws_manager.subscribe(list(added))
                        
                        if added or removed:
                            notification_service.send_list_change_notification(
                                added=added, removed=removed, is_volume_on=pair["is_volume_on"], is_local_test=False
                            )
                        pair["strong_targets"] = new_top_20_targets

                    notification_service.send_list_change_notification(
                        added=new_top_20_targets, removed=set(), webhook_type="general_targets", is_local_test=False
                    )
                    
                    fetched_times_today.add((today_str, fetch_time_str))

            # Heartbeat notification
            current_minute = now.minute
            if current_minute % 15 == 0 and current_minute != last_heartbeat_minute:
                if not position_manager_volume_on.get_open_positions_symbols() and not position_manager_volume_off.get_open_positions_symbols():
                    logging.info("HEARTBEAT_CHECK: No open positions. Sending notification.")
                    notification_service.send_heartbeat_notification(message="沒有艙位")
                last_heartbeat_minute = current_minute

            try:
                message = ws_manager.get_message(block=False)
                if message:
                    kline = message['k']
                    symbol = message['s']
                    for pair in strategy_pairs.values():
                        process_kline_message(
                            symbol, kline,
                            pair["position_manager"],
                            pair["alligator_strategy"],
                            pair["strong_targets"],
                            pair["is_volume_on"],
                        )
            except queue.Empty:
                pass

            time.sleep(1)

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
