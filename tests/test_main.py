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
    live.shadow_snapshot.side_effect = lambda cutoff, top, rows, **kw: [{"date": str(cutoff.date()), "symbol": "AAAUSDT", "s1": 0.2}]
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


def test_entry_job_records_exchange_fill():
    live, pm = MagicMock(), MagicMock()
    pm.get_open_positions_symbols.return_value = []
    pm.open_notional.return_value = {"exchange": "filled", "order_id": 7, "fill_price": 2.01}
    live.on_closed_kline.return_value = "BUY"
    live.entry_features.return_value = {"s1": 0.3}
    main.handle_kline(_msg("AAAUSDT", 0, c=2.0), live, pm, {"NOTIONAL_PCT": 0.04, "DISASTER_STOP_PCT": 0.2})
    row = pd.read_csv(main.SHADOW_ENTRIES_CSV).iloc[0]
    assert row["exchange"] == "filled" and row["order_id"] == 7 and row["fill_price"] == 2.01


def test_drain_shadow_waits_then_gives_up():
    import threading
    threading.Thread(target=main._shadow_worker, daemon=True).start()
    done, gate = [], threading.Event()
    main._SHADOW_Q.put(lambda: done.append(1))
    assert main._drain_shadow(timeout=2) is True and done == [1]
    main._SHADOW_Q.put(gate.wait)                                          # 卡住的工作 → 時間到就放棄，不讓程式關不掉
    assert main._drain_shadow(timeout=0.3) is False
    gate.set()
    main._SHADOW_Q.join()


# ---------- v3.1：持倉上限、BTC 且寬度開關、開關關掉就全平（docs/shotgun-v3.1-gate.md） ----------

def _ms(text):
    return int(pd.Timestamp(text).value // 1_000_000)


def test_handle_kline_passes_max_positions():
    live, pm = MagicMock(), MagicMock()
    pm.get_open_positions_symbols.return_value = []
    live.on_closed_kline.return_value = "BUY"
    cfg = {"NOTIONAL_PCT": 0.10, "DISASTER_STOP_PCT": 0.2, "MAX_POSITIONS": 10}
    main.handle_kline(_msg("AAAUSDT", 0, c=2.0), live, pm, cfg)
    assert pm.open_notional.call_args.args[2:] == (pd.Timestamp(3_600_000, unit="ms"), 0.10, 0.2)
    assert pm.open_notional.call_args.kwargs["max_positions"] == 10


def test_enforce_gate_closes_everything_when_gate_is_off():
    live, pm, notif = MagicMock(), MagicMock(), MagicMock()
    live.gate_off_reason.return_value = "breadth"
    live.breadth_on = 0.5
    live.price_for.side_effect = {"AAAUSDT": 2.0, "BBBUSDT": None}.get
    pm.get_open_positions_symbols.return_value = ["AAAUSDT", "BBBUSDT"]
    pm.close_all.return_value = ["AAAUSDT", "BBBUSDT"]
    now = pd.Timestamp("2026-01-02 05:00:01")
    assert main.enforce_gate(live, pm, notif, now) == ["AAAUSDT", "BBBUSDT"]
    prices, ts, reason = pm.close_all.call_args.args
    assert prices == {"AAAUSDT": 2.0} and ts == now and reason == "GATE_OFF_BREADTH"
    assert live.gate_off_reason.call_args.args == (_ms("2026-01-02 05:00:01"),)
    assert "全部平倉 2 檔" in notif.send_heartbeat_notification.call_args.kwargs["message"]


def test_enforce_gate_keeps_positions_when_gate_on_or_data_missing():
    live, pm, notif = MagicMock(), MagicMock(), MagicMock()
    live.gate_off_reason.return_value = None
    pm.get_open_positions_symbols.return_value = ["AAAUSDT"]
    assert main.enforce_gate(live, pm, notif, pd.Timestamp("2026-01-02 05:00")) == []
    pm.close_all.assert_not_called()
    notif.send_heartbeat_notification.assert_not_called()


def test_refresh_breadth_updates_base_only_at_new_4h_closes():
    live = MagicMock()
    live.breadth_smas = {}
    live.breadth_base_ms = _ms("2026-01-02 04:00")
    main.refresh_breadth(live, pd.Timestamp("2026-01-02 05:00:01"))
    live.refresh_breadth_base.assert_not_called()                                   # 05:00 不是 4h 收盤
    live.refresh_breadth.assert_called_once_with(_ms("2026-01-02 05:00:01"))
    main.refresh_breadth(live, pd.Timestamp("2026-01-02 08:00:01"))
    live.refresh_breadth_base.assert_called_once_with(_ms("2026-01-02 08:00"))
    live.refresh_breadth_base.reset_mock()
    live.breadth_base_ms = None                                                     # 啟動或上次失敗 → 補算最近一根 4h
    main.refresh_breadth(live, pd.Timestamp("2026-01-02 13:30"))
    live.refresh_breadth_base.assert_called_once_with(_ms("2026-01-02 12:00"))


def test_report_gate_notifies_only_on_change():
    live, notif = MagicMock(), MagicMock()
    live.days = {}
    live.breadth_on = 0.5
    live.breadth_now.return_value = 0.42
    live.gate_state.return_value = "off"
    now = pd.Timestamp("2026-01-02 05:00")
    assert main.report_gate(live, notif, now, "on") == "off"
    message = notif.send_heartbeat_notification.call_args.kwargs["message"]
    assert "OFF" in message and "42%" in message
    notif.reset_mock()
    assert main.report_gate(live, notif, now, "off") == "off"
    notif.send_heartbeat_notification.assert_not_called()


def test_handle_batch_handles_exits_before_entries():
    """持倉滿格時，同一小時出場空出的格子要給同一小時的進場：先處理有持倉的幣，再依幣名處理其他幣。"""
    live, pm, order = MagicMock(), MagicMock(), []
    pm.get_open_positions_symbols.return_value = ["ZZZUSDT"]
    live.on_closed_kline.side_effect = lambda symbol, open_ms, close, has_position: "SELL" if has_position else "BUY"
    pm.close_by_signal.side_effect = lambda symbol, *a, **k: order.append(("SELL", symbol))
    pm.open_notional.side_effect = lambda symbol, *a, **k: order.append(("BUY", symbol))
    cfg = {"NOTIONAL_PCT": 0.10, "DISASTER_STOP_PCT": 0.2, "MAX_POSITIONS": 1}
    msgs = [_msg("BBBUSDT", 0), _msg("ZZZUSDT", 0), _msg("AAAUSDT", 0)]
    assert main.handle_batch(msgs, live, pm, cfg) == ["SELL", "BUY", "BUY"]
    assert order == [("SELL", "ZZZUSDT"), ("BUY", "AAAUSDT"), ("BUY", "BBBUSDT")]


def test_collect_batch_waits_for_the_same_bar_until_all_arrive_or_timeout():
    class FakeWs:
        def __init__(self, msgs):
            self.msgs = list(msgs)

        def get_message(self, block=True, timeout=None):
            return self.msgs.pop(0) if self.msgs else None

    ticks = iter(range(100))
    clock = lambda: next(ticks)                                                      # noqa: E731  每次呼叫 +1 秒
    ws = FakeWs([_msg("BBBUSDT", 0), _msg("XXXUSDT", 3_600_000), _msg("CCCUSDT", 0)])
    batch, others = main.collect_batch(ws, _msg("AAAUSDT", 0), {"AAAUSDT", "BBBUSDT", "CCCUSDT"}, wait=10, clock=clock)
    assert [m["s"] for m in batch] == ["AAAUSDT", "BBBUSDT", "CCCUSDT"] and [m["s"] for m in others] == ["XXXUSDT"]
    ws = FakeWs([_msg("BBBUSDT", 0)])                                                # DDD 沒來 → 等滿就放行
    batch, _ = main.collect_batch(ws, _msg("AAAUSDT", 0), {"AAAUSDT", "BBBUSDT", "DDDUSDT"}, wait=5, clock=clock)
    assert [m["s"] for m in batch] == ["AAAUSDT", "BBBUSDT"]


def test_subscribe_open_positions_adds_only_missing_symbols():
    """換日後處理的 23:00 那根買進了剛被取消訂閱的幣 → 要補訂閱，否則收不到 K 棒、無法訊號出場。"""
    ws, pm = MagicMock(), MagicMock()
    pm.get_open_positions_symbols.return_value = ["AAAUSDT", "BBBUSDT"]
    subscribed = {"AAAUSDT", "CCCUSDT"}
    assert main.subscribe_open_positions(ws, pm, subscribed) == {"BBBUSDT"}
    ws.subscribe.assert_called_once_with(["BBBUSDT"])
    assert subscribed == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}
    ws.reset_mock()
    assert main.subscribe_open_positions(ws, pm, subscribed) == set()
    ws.subscribe.assert_not_called()


def test_hourly_gate_refreshes_then_reports_then_enforces():
    live, pm, notif, order = MagicMock(), MagicMock(), MagicMock(), []
    live.days, live.breadth_on, live.breadth_smas, live.breadth_base_ms = {}, 0.5, {}, _ms("2026-01-02 04:00")
    live.refresh_breadth.side_effect = lambda now_ms: order.append("refresh") or 0.4
    live.gate_state.side_effect = lambda now_ms: order.append("report") or "off"
    live.breadth_now.return_value = 0.4
    live.gate_off_reason.side_effect = lambda now_ms: order.append("enforce") or "breadth"
    pm.get_open_positions_symbols.return_value = []
    assert main.hourly_gate(live, pm, notif, pd.Timestamp("2026-01-02 05:00:01"), "on") == "off"
    assert order == ["refresh", "report", "enforce"]
