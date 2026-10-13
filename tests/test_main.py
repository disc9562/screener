from unittest.mock import MagicMock

import pandas as pd

import main
from services.websocket_manager import FUTURES_STREAM_URL, WebSocketManager


def _msg(symbol, open_ms, o=1.0, h=1.1, l=0.9, c=1.0, closed=True):
    return {"s": symbol, "k": {"t": open_ms, "o": str(o), "h": str(h), "l": str(l), "c": str(c), "x": closed}}


def test_next_cutoff_is_next_utc_midnight():
    assert main.next_cutoff(pd.Timestamp("2026-01-01 13:45")) == pd.Timestamp("2026-01-02")
    assert main.next_cutoff(pd.Timestamp("2026-01-02 00:00")) == pd.Timestamp("2026-01-03")


def test_handle_kline_routes_buy_and_sell():
    live, pm = MagicMock(), MagicMock()
    cfg = {"NOTIONAL_PCT": 0.04, "DISASTER_STOP_PCT": 0.2}
    pm.get_open_positions_symbols.return_value = []
    live.on_closed_kline.return_value = "BUY"
    assert main.handle_kline(_msg("AAAUSDT", 0, c=2.0), live, pm, cfg) == "BUY"
    pm.open_notional.assert_called_once()
    assert pm.open_notional.call_args.args[:2] == ("AAAUSDT", 2.0)
    pm.get_open_positions_symbols.return_value = ["AAAUSDT"]
    live.on_closed_kline.return_value = "SELL"
    assert main.handle_kline(_msg("AAAUSDT", 3_600_000, c=2.2), live, pm, cfg) == "SELL"
    pm.close_by_signal.assert_called_once()


def test_bootstrap_subscribes_open_positions():
    live, pm = MagicMock(), MagicMock()
    live.daily_rebalance.return_value = (["AAAUSDT"], True)
    pm.get_open_positions_symbols.return_value = ["ZZZUSDT"]
    syms, ok = main.bootstrap(live, pm, pd.Timestamp("2026-01-01 05:00"))
    assert ok and syms == {"AAAUSDT", "ZZZUSDT"}
    warmed = {c.args[0] for c in live.warmup.call_args_list}
    assert warmed == {"AAAUSDT", "ZZZUSDT"}
    assert live.daily_rebalance.call_args_list[-1].args[0] == pd.Timestamp("2026-01-01")


def test_websocket_defaults_to_futures_stream():
    ws = WebSocketManager("k", "s", MagicMock())
    assert ws.stream_url == FUTURES_STREAM_URL


def test_rollover_failure_alerts_and_keeps_cutoff():
    live, pm, ws, notif = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    live.daily_rebalance.side_effect = RuntimeError("binance down")
    assert main.rollover(live, pm, ws, notif, pd.Timestamp("2026-01-02"), set()) is False
    assert notif.send_heartbeat_notification.called
    ws.subscribe.assert_not_called()


def test_rollover_success_warms_missing_and_subscribes_new():
    live, pm, ws, notif = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    live.daily_rebalance.return_value = (["AAAUSDT", "BBBUSDT"], True)
    live.states = {"AAAUSDT": object()}
    pm.get_open_positions_symbols.return_value = ["ZZZUSDT"]
    subscribed = {"AAAUSDT", "ZZZUSDT"}
    assert main.rollover(live, pm, ws, notif, pd.Timestamp("2026-01-02"), subscribed) is True
    assert {c.args[0] for c in live.warmup.call_args_list} == {"BBBUSDT", "ZZZUSDT"}
    ws.subscribe.assert_called_once_with(["BBBUSDT"])
    assert subscribed == {"AAAUSDT", "BBBUSDT", "ZZZUSDT"}


def test_bootstrap_survives_rebalance_failure():
    live, pm = MagicMock(), MagicMock()
    live.daily_rebalance.side_effect = RuntimeError("binance down")
    pm.get_open_positions_symbols.return_value = ["ZZZUSDT"]
    syms, ok = main.bootstrap(live, pm, pd.Timestamp("2026-01-01 05:00"))
    assert ok is False and syms == {"ZZZUSDT"}
    live.warmup.assert_called_once_with("ZZZUSDT")


def test_order_execution_disabled_in_hedge_mode(monkeypatch):
    svc = MagicMock()
    svc.is_one_way_mode.return_value = False
    monkeypatch.setattr(main, "USE_TESTNET", True)
    monkeypatch.setattr(main, "BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setattr(main, "BINANCE_TESTNET_API_SECRET", "s")
    monkeypatch.setattr(main, "OrderExecutionService", MagicMock(return_value=svc))
    assert main._init_order_execution_service() is None
    svc.is_one_way_mode.return_value = True
    assert main._init_order_execution_service() is svc
