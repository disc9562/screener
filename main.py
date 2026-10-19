import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
import queue
import argparse
import threading
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from config import (
    get_strategy_config, BINANCE_API_KEY, BINANCE_API_SECRET,
    USE_TESTNET, BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET,
    NOTIONAL_PCT, DISASTER_STOP_PCT, EXCLUDE_SYMBOLS, EQUITY_LIKE_BASES, POSITIONS_CSV_V3,
    SHADOW_LISTS_CSV, SHADOW_ENTRIES_CSV, RS_MODE, MIN_DAILY_QUOTE_VOL,
)
from data.fetcher import CryptoFetcher
from strategy.shotgun_v3_live import ShotgunV3Live
from services.position_manager import PositionManager
from services.websocket_manager import WebSocketManager
from services.notification_service import NotificationService
from services.order_execution_service import OrderExecutionService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[RotatingFileHandler('application.log', maxBytes=20 * 1024 * 1024, backupCount=5, encoding='utf-8')],
)


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


def append_csv(path, rows):
    """影子記錄：累加寫入 CSV（只有第一次寫表頭）。"""
    if not rows:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pd.DataFrame(rows).to_csv(path, mode="a", header=not os.path.exists(path), index=False, encoding="utf-8")


SHADOW_DELAY = pd.Timedelta(minutes=10)   # 每日快照在換日後 10 分鐘才開始抓（約 2N 權重）
_SHADOW_Q = queue.Queue()
_SHADOW_WORKER = None


def _shadow_worker():
    while True:
        fn = _SHADOW_Q.get()
        try:
            fn()
        except Exception as e:
            logging.error(f"Shadow job crashed (trading unaffected): {e}", exc_info=True)
        finally:
            _SHADOW_Q.task_done()


def _start_shadow(fn):
    """影子工作排進單一背景佇列：主迴圈不等它、只有一個寫入者。程式結束時佇列裡沒做完的會丟掉（只是記錄）。"""
    global _SHADOW_WORKER
    if _SHADOW_WORKER is None or not _SHADOW_WORKER.is_alive():
        _SHADOW_WORKER = threading.Thread(target=_shadow_worker, daemon=True, name="shadow")
        _SHADOW_WORKER.start()
    _SHADOW_Q.put(fn)


def _drain_shadow(timeout=60):
    """程式結束前等影子佇列做完（最多 timeout 秒，不讓卡住的工作擋住關機）；回傳是否全部做完。"""
    deadline = time.time() + timeout
    while _SHADOW_Q.unfinished_tasks and time.time() < deadline:
        time.sleep(0.05)
    return not _SHADOW_Q.unfinished_tasks


def _logged_dates(path):
    if not os.path.exists(path):
        return set()
    try:
        return set(pd.read_csv(path, usecols=["date"])["date"].astype(str))
    except Exception:
        return set()


def _entry_job(live, symbol, open_ms, ts, close, opened):
    """進場的影子特徵（背景執行）：signal_close 是訊號收盤；exchange／order_id／fill_price 是交易所實際成交（紙上模式為 disabled）。"""
    try:
        info = opened if isinstance(opened, dict) else {}
        feats = live.entry_features(symbol, open_ms + 3_600_000)
        append_csv(SHADOW_ENTRIES_CSV, [dict(time=str(ts), symbol=symbol, signal_close=close, paper_opened=bool(opened),
                                             exchange=info.get("exchange"), order_id=info.get("order_id"),
                                             fill_price=info.get("fill_price"), **feats)])
    except Exception as e:
        logging.error(f"Shadow entry features for {symbol} failed (trading unaffected): {e}")


def _shadow_job(live, cutoff, top, rows_4h, universe=None):
    """背景執行：失敗只記 log，不影響交易；同一天已記過就跳過（換日重試、重啟補記不會重複）。"""
    try:
        if str(cutoff.date()) in _logged_dates(SHADOW_LISTS_CSV):
            logging.info(f"Shadow lists {cutoff.date()} already logged, skip")
            return
        wait = (cutoff + SHADOW_DELAY - pd.Timestamp.now(tz="UTC").tz_localize(None)).total_seconds()
        if wait > 0:                                            # 避開換日、暖機的請求尖峰（限速器沒有優先權）
            time.sleep(wait)
        t0 = time.time()
        rows = live.shadow_snapshot(cutoff, top, rows_4h, universe=universe)
        append_csv(SHADOW_LISTS_CSV, rows)
        logging.info(f"Shadow lists {cutoff.date()}: {len(rows)} rows in {time.time() - t0:.0f}s")
    except Exception as e:
        logging.error(f"Shadow snapshot {cutoff.date()} failed (trading unaffected): {e}", exc_info=True)


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
        opened = position_manager.open_notional(symbol, close, ts, cfg["NOTIONAL_PCT"], cfg["DISASTER_STOP_PCT"])
        _start_shadow(lambda: _entry_job(live, symbol, open_ms, ts, close, opened))   # 影子記錄排背景，不拖主迴圈
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
        rows_4h, universe = dict(live.rows_4h), list(live.universe)
        _start_shadow(lambda: _shadow_job(live, today, list(top), rows_4h, universe))       # 啟動時補記當日快照（已記過會跳過）
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
    active = set(top) | set(position_manager.get_open_positions_symbols())
    for s in active - set(live.states):
        live.warmup(s)
    new = set(top) - subscribed
    if new:
        ws.subscribe(sorted(new))
        subscribed |= new
    stale = subscribed - active
    if stale:
        if hasattr(ws, "unsubscribe"):
            ws.unsubscribe(sorted(stale))
        subscribed -= stale
        if isinstance(live.states, dict):
            for s in stale:
                live.states.pop(s, None)
    notification_service.send_list_change_notification(added=set(top), removed=stale, webhook_type="general_targets")
    logging.info(f"Daily rebalance {cutoff.date()} bull={bull} list={top}")
    rows_4h, universe = dict(live.rows_4h), list(live.universe)
    _start_shadow(lambda: _shadow_job(live, cutoff, list(top), rows_4h, universe))
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
    live = ShotgunV3Live(CryptoFetcher(), exclude_symbols=EXCLUDE_SYMBOLS, exclude_bases=EQUITY_LIKE_BASES,
                         shadow_fetcher=CryptoFetcher(), rs_mode=RS_MODE, min_daily_quote_vol=MIN_DAILY_QUOTE_VOL)
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
        if not _drain_shadow(60):
            logging.warning("Shadow jobs left unfinished at shutdown (records only; trading unaffected)")
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
