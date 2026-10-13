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
    NOTIONAL_PCT, DISASTER_STOP_PCT, EXCLUDE_SYMBOLS, EQUITY_LIKE_BASES, POSITIONS_CSV_V3,
)
from data.fetcher import CryptoFetcher
from strategy.shotgun_v3_live import ShotgunV3Live
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService
from services.order_execution_service import OrderExecutionService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='application.log', filemode='w')


def _init_order_execution_service():
    """Initialize testnet order execution if enabled and configured."""
    if not USE_TESTNET:
        logging.info("Testnet order execution DISABLED (paper trading mode)")
        return None
    if not BINANCE_TESTNET_API_KEY or not BINANCE_TESTNET_API_SECRET:
        logging.warning("USE_TESTNET is enabled but BINANCE_TESTNET_API_KEY/SECRET not set. Running without order execution.")
        return None
    try:
        service = OrderExecutionService(BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET)
        if not service.is_one_way_mode():
            logging.error("Account is in hedge (dual-side) position mode: reduceOnly orders would fail. "
                          "Switch to one-way mode. Order execution DISABLED.")
            return None
        return service
    except Exception as e:
        logging.error(f"Failed to initialize OrderExecutionService: {e}")
        logging.warning("Continuing without testnet order execution.")
        return None


def next_cutoff(now: pd.Timestamp) -> pd.Timestamp:
    return now.normalize() + pd.Timedelta(days=1)


def handle_kline(message, live, position_manager, cfg):
    """收完的 1h K 棒：先用低點檢查紙上災難止損，再交給策略判斷 BUY/SELL。"""
    k, symbol = message['k'], message['s']
    open_ms, close = int(k['t']), float(k['c'])
    ts = pd.to_datetime(open_ms, unit='ms') + pd.Timedelta(hours=1)
    if symbol in position_manager.get_open_positions_symbols():
        position_manager.update_positions({symbol: pd.Series({
            'Datetime': ts, 'High': float(k['h']), 'Low': float(k['l']), 'Close': close})})
    has_position = symbol in position_manager.get_open_positions_symbols()
    action = live.on_closed_kline(symbol, open_ms, close, has_position)
    if action == "SELL":
        position_manager.close_by_signal(symbol, close, ts, "CROSS_10_20")
    elif action == "BUY":
        position_manager.open_notional(symbol, close, ts, cfg["NOTIONAL_PCT"], cfg["DISASTER_STOP_PCT"])
    return action


def bootstrap(live, position_manager, now):
    """啟動：算今天（以及前一天，給 23:00 那根用）的名單與開關，暖機名單 ∪ 持倉。
    回傳 (要訂閱的幣, 今日名單是否算成)；算不成時仍暖機持倉，讓出場照常運作。"""
    today = now.normalize()
    symbols = set(position_manager.get_open_positions_symbols())
    ok = True
    try:
        live.daily_rebalance(today - pd.Timedelta(days=1))
        top, _ = live.daily_rebalance(today)
        symbols |= set(top)
    except Exception as e:
        logging.error(f"Startup rebalance failed: {e}", exc_info=True)
        ok = False
    for s in symbols:
        live.warmup(s)
    return symbols, ok


REBALANCE_RETRY = pd.Timedelta(minutes=5)


def rollover(live, position_manager, ws, notification_service, cutoff, subscribed):
    """換日：算 cutoff 當日的名單與開關。失敗 → 警示並回傳 False（呼叫端不推進 cutoff，5 分鐘後重試；
    期間當日名單為空 → 不進新倉，但持倉的出場照常）。成功 → 暖機「名單 ∪ 持倉」中沒有狀態的幣、訂閱新幣。"""
    try:
        top, bull = live.daily_rebalance(cutoff)
    except Exception as e:
        logging.error(f"Daily rebalance {cutoff.date()} failed: {e}", exc_info=True)
        notification_service.send_heartbeat_notification(
            message=f"⚠️ {cutoff.date()} 名單計算失敗，5 分鐘後重試：{e}")
        return False
    for s in (set(top) | set(position_manager.get_open_positions_symbols())) - set(live.states):
        live.warmup(s)
    new = set(top) - subscribed
    if new:
        ws.subscribe(sorted(new))
        subscribed |= new
    notification_service.send_list_change_notification(added=set(top), removed=set(), webhook_type="general_targets")
    logging.info(f"Daily rebalance {cutoff.date()} bull={bull} list={top}")
    return True


def run_app(args):
    logging.info("Shotgun v3 starting...")
    strategy_config = get_strategy_config()
    webhook_on = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_ON")
    webhook_off = strategy_config.get("DISCORD_WEBHOOK_URL_VOLUME_OFF")
    if not webhook_on or not webhook_off:
        logging.error("Error: Both DISCORD_WEBHOOK_URL_VOLUME_ON and DISCORD_WEBHOOK_URL_VOLUME_OFF must be configured.")
        sys.exit(1)
    notification_service = NotificationService(webhook_urls_map={
        "volume_on": webhook_on, "volume_off": webhook_off,
        "general_targets": strategy_config.get("DISCORD_WEBHOOK_URL_GENERAL_TARGETS"),
    })
    cfg = {"NOTIONAL_PCT": NOTIONAL_PCT, "DISASTER_STOP_PCT": DISASTER_STOP_PCT}
    position_manager = PositionManager(notification_service, strategy_config, POSITIONS_CSV_V3,
                                       order_execution_service=_init_order_execution_service())
    live = ShotgunV3Live(CryptoFetcher(), exclude_symbols=EXCLUDE_SYMBOLS, exclude_bases=EQUITY_LIKE_BASES)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    subscribed, ok = bootstrap(live, position_manager, now)
    cutoff = next_cutoff(now) if ok else now.normalize()      # 今日名單沒算成 → 進迴圈後立刻重試
    retry_at = now
    notification_service.send_list_change_notification(added=set(subscribed), removed=set(), webhook_type="general_targets")
    ws = WebSocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET, notification_service=notification_service)
    ws.start(symbols=sorted(subscribed), interval='1h')
    try:
        while True:
            now = pd.Timestamp.now(tz="UTC").tz_localize(None)
            if now >= cutoff and now >= retry_at:
                if rollover(live, position_manager, ws, notification_service, cutoff, subscribed):
                    cutoff = next_cutoff(now)
                else:
                    retry_at = now + REBALANCE_RETRY
            message = ws.get_message(block=True, timeout=1)
            if message:
                try:
                    handle_kline(message, live, position_manager, cfg)
                except Exception as e:
                    logging.error(f"handle_kline failed for {message.get('s')}: {e}", exc_info=True)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        ws.stop()
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
