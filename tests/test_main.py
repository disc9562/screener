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
    syms = main.bootstrap(live, pm, pd.Timestamp("2026-01-01 05:00"))
    assert syms == {"AAAUSDT", "ZZZUSDT"}
    warmed = {c.args[0] for c in live.warmup.call_args_list}
    assert warmed == {"AAAUSDT", "ZZZUSDT"}
    assert live.daily_rebalance.call_args_list[-1].args[0] == pd.Timestamp("2026-01-01")


def test_websocket_defaults_to_futures_stream():
    ws = WebSocketManager("k", "s", MagicMock())
    assert ws.stream_url == FUTURES_STREAM_URL
