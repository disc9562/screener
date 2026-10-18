from unittest.mock import MagicMock

import pandas as pd
import pytest

import main
from services.websocket_manager import FUTURES_STREAM_URL, WebSocketManager


@pytest.fixture(autouse=True)
def _shadow_to_tmp(tmp_path, monkeypatch):
    """影子記錄寫到暫存目錄、同步執行（正式環境是背景執行緒）。"""
    monkeypatch.setattr(main, "SHADOW_LISTS_CSV", str(tmp_path / "shadow_lists.csv"))
    monkeypatch.setattr(main, "SHADOW_ENTRIES_CSV", str(tmp_path / "shadow_entries.csv"))
    monkeypatch.setattr(main, "_start_shadow", lambda fn: fn())


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


def _rollover_ok(live):
    pm, ws, notif = MagicMock(), MagicMock(), MagicMock()
    pm.get_open_positions_symbols.return_value = []
    return main.rollover(live, pm, ws, notif, pd.Timestamp("2026-01-02"), set())


def test_shadow_rows_appended_with_single_header():
    live = MagicMock()
    live.daily_rebalance.return_value = (["AAAUSDT"], True)
    live.states = {}
    live.shadow_snapshot.side_effect = lambda cutoff, top, rows: [{"date": str(cutoff.date()), "symbol": "AAAUSDT", "s1": 0.2}]
    pm, ws, notif = MagicMock(), MagicMock(), MagicMock()
    pm.get_open_positions_symbols.return_value = []
    for day in ("2026-01-02", "2026-01-03", "2026-01-03"):           # 第二次 01-03 是重跑，不能重複寫
        assert main.rollover(live, pm, ws, notif, pd.Timestamp(day), set())
    df = pd.read_csv(main.SHADOW_LISTS_CSV)
    assert list(df["date"]) == ["2026-01-02", "2026-01-03"]


def test_shadow_failure_does_not_break_rollover():
    live = MagicMock()
    live.daily_rebalance.return_value = (["AAAUSDT"], True)
    live.states = {}
    live.shadow_snapshot.side_effect = RuntimeError("binance down")
    assert _rollover_ok(live) is True


def test_buy_logs_entry_features_and_survives_failure():
    live, pm = MagicMock(), MagicMock()
    cfg = {"NOTIONAL_PCT": 0.04, "DISASTER_STOP_PCT": 0.2}
    pm.get_open_positions_symbols.return_value = []
    live.on_closed_kline.return_value = "BUY"
    live.entry_features.return_value = {"s1": 0.21, "ladder": 1}
    pm.open_notional.return_value = True
    assert main.handle_kline(_msg("AAAUSDT", 0, c=2.0), live, pm, cfg) == "BUY"
    df = pd.read_csv(main.SHADOW_ENTRIES_CSV)
    assert bool(df.loc[0, "paper_opened"]) and df.loc[0, "signal_close"] == 2.0
    assert df.loc[0, "symbol"] == "AAAUSDT" and df.loc[0, "s1"] == 0.21
    assert live.entry_features.call_args.args == ("AAAUSDT", 3_600_000)   # 訊號根收盤 = 開盤 + 1h
    live.entry_features.side_effect = RuntimeError("binance down")
    assert main.handle_kline(_msg("BBBUSDT", 0, c=2.0), live, pm, cfg) == "BUY"
    assert pm.open_notional.call_count == 2


def test_entry_features_never_fetched_on_the_trading_loop(monkeypatch):
    jobs = []
    monkeypatch.setattr(main, "_start_shadow", jobs.append)          # 背景佇列：只排隊、不在主迴圈執行
    live, pm = MagicMock(), MagicMock()
    pm.get_open_positions_symbols.return_value = []
    live.on_closed_kline.return_value = "BUY"
    live.entry_features.return_value = {"s1": 0.3}
    main.handle_kline(_msg("AAAUSDT", 0, c=2.0), live, pm, {"NOTIONAL_PCT": 0.04, "DISASTER_STOP_PCT": 0.2})
    assert live.entry_features.call_count == 0 and len(jobs) == 1
    jobs[0]()
    assert live.entry_features.call_count == 1


def test_bootstrap_queues_todays_snapshot():
    live, pm = MagicMock(), MagicMock()
    live.daily_rebalance.return_value = (["AAAUSDT"], True)
    live.shadow_snapshot.return_value = [{"date": "2026-01-01", "symbol": "AAAUSDT"}]
    pm.get_open_positions_symbols.return_value = []
    main.bootstrap(live, pm, pd.Timestamp("2026-01-01 05:00"))
    assert pd.read_csv(main.SHADOW_LISTS_CSV)["date"].tolist() == ["2026-01-01"]


def test_shadow_worker_survives_errors_and_runs_in_order():
    import threading
    done = []
    threading.Thread(target=main._shadow_worker, daemon=True).start()
    main._SHADOW_Q.put(lambda: 1 / 0)                               # 某個影子工作壞掉
    main._SHADOW_Q.put(lambda: done.append("next"))                 # 後面的照常執行
    main._SHADOW_Q.join()
    assert done == ["next"]
