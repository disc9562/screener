from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from services.order_execution_service import OrderExecutionService
from services.position_manager import PositionManager


def _pm(tmp_path, oes=None):
    notif = MagicMock()
    cfg = {"TOTAL_CAPITAL": 10000.0, "TRANSACTION_FEE_PERCENT": 0.0005}
    return PositionManager(notif, cfg, str(tmp_path / "p.csv"), order_execution_service=oes), notif


def test_open_notional_sizes_by_equity_and_sets_disaster_stop(tmp_path):
    pm, _ = _pm(tmp_path)
    assert pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    row = pm.positions_df.iloc[-1]
    assert row["units"] == 10000.0 * 0.04 / 2.0 and row["stop_loss"] == 2.0 * 0.8 and pd.isna(row["take_profit"])
    assert not pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)   # 同幣不重複


def test_close_by_signal_books_pnl_and_equity(tmp_path):
    pm, _ = _pm(tmp_path)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    assert pm.close_by_signal("AAAUSDT", 2.2, pd.Timestamp("2026-01-02"))
    pnl = 200 * 0.2 - 200 * 2.0 * 0.0005 - 200 * 2.2 * 0.0005
    assert abs(pm.positions_df.iloc[-1]["pnl"] - pnl) < 1e-9
    assert abs(pm.paper_equity() - (10000.0 + pnl)) < 1e-9
    assert pm.get_open_positions_symbols() == []


def test_paper_disaster_stop_via_update_positions(tmp_path):
    pm, _ = _pm(tmp_path)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    k = pd.Series({"Datetime": pd.Timestamp("2026-01-02"), "High": 2.0, "Low": 1.5, "Close": 1.7})
    pm.update_positions({"AAAUSDT": k})
    assert pm.positions_df.iloc[-1]["exit_reason"] == "STOP_LOSS"


def test_testnet_orders_entry_and_stop_only(tmp_path):
    oes = MagicMock()
    pm, _ = _pm(tmp_path, oes)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    oes.place_market_order.assert_called_once_with("AAAUSDT", "BUY", 200.0)
    oes.place_stop_loss_order.assert_called_once_with("AAAUSDT", "SELL", 200.0, 1.6)
    oes.place_take_profit_order.assert_not_called()


def test_close_position_is_reduce_only():
    svc = OrderExecutionService.__new__(OrderExecutionService)
    svc.client = MagicMock()
    svc._symbol_info_cache = {"AAAUSDT": {"quantityPrecision": 3, "pricePrecision": 4, "stepSize": 0.001, "tickSize": 0.0001}}
    svc.close_position("AAAUSDT", 200.0)
    kwargs = svc.client.futures_create_order.call_args.kwargs
    assert kwargs["reduceOnly"] == "true" and kwargs["side"] == "SELL"


def test_close_after_csv_reload_reaches_exchange(tmp_path):
    """C1：重啟後從 CSV 讀回，平倉不得丟 dtype 例外，且要真的送出交易所平倉。"""
    oes = MagicMock()
    pm, _ = _pm(tmp_path, oes)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    pm2 = PositionManager(MagicMock(), {"TOTAL_CAPITAL": 10000.0, "TRANSACTION_FEE_PERCENT": 0.0005},
                          str(tmp_path / "p.csv"), order_execution_service=oes)
    assert pm2.close_by_signal("AAAUSDT", 2.2, pd.Timestamp("2026-01-02"))
    oes.close_position.assert_called_once()
    pm3 = PositionManager(MagicMock(), {"TOTAL_CAPITAL": 10000.0, "TRANSACTION_FEE_PERCENT": 0.0005}, str(tmp_path / "p.csv"))
    assert pm3.get_open_positions_symbols() == []
    assert pm3.open_notional("BBBUSDT", 1.0, pd.Timestamp("2026-01-03"), 0.04, 0.20)
    pm4 = PositionManager(MagicMock(), {"TOTAL_CAPITAL": 10000.0, "TRANSACTION_FEE_PERCENT": 0.0005}, str(tmp_path / "p.csv"))
    k = pd.Series({"Datetime": pd.Timestamp("2026-01-04"), "High": 1.0, "Low": 0.7, "Close": 0.75})
    pm4.update_positions({"BBBUSDT": k})                      # 紙上災難止損路徑（已有 CLOSED 列時）
    assert PositionManager(MagicMock(), {}, str(tmp_path / "p.csv")).get_open_positions_symbols() == []


def _svc():
    svc = OrderExecutionService.__new__(OrderExecutionService)
    svc.client = MagicMock()
    svc._symbol_info_cache = {"AAAUSDT": {"quantityPrecision": 3, "pricePrecision": 4, "stepSize": 0.001, "tickSize": 0.0001}}
    svc._cross_margin_done = set()                       # 同 __init__
    return svc


def test_cancel_open_orders_also_cancels_conditional_algo_orders():
    """C3：STOP_MARKET 會被轉送到 algo 端點，撤單必須也帶 conditional=True。"""
    svc = _svc()
    svc.cancel_open_orders("AAAUSDT")
    calls = [c.kwargs for c in svc.client.futures_cancel_all_open_orders.call_args_list]
    assert {"symbol": "AAAUSDT"} in calls and {"symbol": "AAAUSDT", "conditional": True} in calls


def test_close_position_sells_first_and_cancels_only_after_success():
    """I4：先送 reduceOnly 賣單（最多 3 次），成功後才撤止損；撤單在賣單之後。"""
    svc = _svc()
    events = []

    def create(**kw):
        events.append("sell")
        if events.count("sell") < 3:
            raise Exception("timeout")
        return {"orderId": 1}

    svc.client.futures_create_order.side_effect = create
    svc.client.futures_cancel_all_open_orders.side_effect = lambda **kw: events.append("cancel")
    assert svc.close_position("AAAUSDT", 200.0) == {"orderId": 1}
    assert events == ["sell", "sell", "sell", "cancel", "cancel"]


def test_close_position_keeps_stop_when_sell_keeps_failing():
    svc = _svc()
    svc.client.futures_create_order.side_effect = Exception("down")
    assert svc.close_position("AAAUSDT", 200.0) is None
    svc.client.futures_cancel_all_open_orders.assert_not_called()


def test_stop_placement_retried_then_alerts(tmp_path):
    oes = MagicMock()
    oes.place_stop_loss_order.return_value = None
    pm, notif = _pm(tmp_path, oes)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    assert oes.place_stop_loss_order.call_count == 3
    assert any("止損" in str(c) for c in notif.send_heartbeat_notification.call_args_list)


def test_exchange_close_failure_alerts(tmp_path):
    oes = MagicMock()
    oes.close_position.return_value = None
    pm, notif = _pm(tmp_path, oes)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    pm.close_by_signal("AAAUSDT", 2.2, pd.Timestamp("2026-01-02"))
    assert any("平倉失敗" in str(c) for c in notif.send_heartbeat_notification.call_args_list)


def test_one_way_mode_check():
    """I5：雙向持倉（hedge）模式下 reduceOnly 單全部會失敗，必須偵測。"""
    svc = _svc()
    svc.client.futures_get_position_mode.return_value = {"dualSidePosition": True}
    assert svc.is_one_way_mode() is False
    svc.client.futures_get_position_mode.return_value = {"dualSidePosition": False}
    assert svc.is_one_way_mode() is True


def test_ensure_cross_margin_ignores_already_crossed_and_caches():
    from binance.exceptions import BinanceAPIException
    svc = _svc()
    svc.client.futures_change_margin_type.side_effect = BinanceAPIException(
        MagicMock(), 400, '{"code":-4046,"msg":"No need to change margin type."}')
    assert svc.ensure_cross_margin("AAAUSDT") is True
    assert svc.ensure_cross_margin("AAAUSDT") is True
    svc.client.futures_change_margin_type.assert_called_once_with(symbol="AAAUSDT", marginType="CROSSED")


def test_open_notional_sets_cross_margin_before_entry(tmp_path):
    oes = MagicMock()
    order = []
    oes.ensure_cross_margin.side_effect = lambda s: order.append("margin") or True
    oes.place_market_order.side_effect = lambda *a, **k: order.append("buy") or {"orderId": 1}
    pm, _ = _pm(tmp_path, oes)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    assert order == ["margin", "buy"]


def test_exchange_entry_failure_rolls_back_paper_position(tmp_path):
    for i, fail in enumerate((dict(return_value=None), dict(side_effect=RuntimeError("timeout")))):
        oes = MagicMock()
        oes.place_market_order.configure_mock(**fail)
        pm, notif = _pm(tmp_path / str(i), oes)
        assert not pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
        assert pm.get_open_positions_symbols() == [] and len(pm.positions_df) == 0   # 交易所沒倉 → 紙上也不留
        assert notif.send_heartbeat_notification.called
        oes.place_stop_loss_order.assert_not_called()


def test_entry_records_order_id_and_fill_price(tmp_path):
    oes = MagicMock()
    oes.place_market_order.return_value = {"orderId": 7, "avgPrice": "2.01", "status": "FILLED"}
    pm, _ = _pm(tmp_path, oes)
    info = pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)
    assert info == {"exchange": "filled", "order_id": 7, "fill_price": 2.01}
    pm2, _ = _pm(tmp_path, oes)                                                  # 重新讀 CSV 也還在
    row = pm2.positions_df.iloc[-1]
    assert str(row["entry_order_id"]) == "7" and float(row["entry_fill_price"]) == 2.01


def test_paper_only_mode_reports_exchange_disabled(tmp_path):
    pm, _ = _pm(tmp_path)
    assert pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.04, 0.20)["exchange"] == "disabled"


# ---------- v3.1：持倉上限、下單權益、全部平倉（docs/shotgun-v3.1-gate.md） ----------

def test_open_notional_respects_max_positions(tmp_path):
    pm, _ = _pm(tmp_path)
    ts = pd.Timestamp("2026-01-01")
    assert pm.open_notional("AAAUSDT", 2.0, ts, 0.10, 0.20, max_positions=2)
    assert pm.open_notional("BBBUSDT", 2.0, ts, 0.10, 0.20, max_positions=2)
    assert not pm.open_notional("CCCUSDT", 2.0, ts, 0.10, 0.20, max_positions=2)       # 已滿
    pm.close_by_signal("AAAUSDT", 2.0, pd.Timestamp("2026-01-02"))
    assert pm.open_notional("CCCUSDT", 2.0, ts, 0.10, 0.20, max_positions=2)


def test_sizing_equity_counts_floating_losses_not_gains(tmp_path):
    pm, _ = _pm(tmp_path)
    pm.open_notional("AAAUSDT", 2.0, pd.Timestamp("2026-01-01"), 0.10, 0.20)            # 500 單位
    fee = 500 * 2.0 * 0.0005
    k = pd.Series({"Datetime": pd.Timestamp("2026-01-02"), "High": 2.0, "Low": 1.8, "Close": 1.8})
    pm.update_positions({"AAAUSDT": k})
    assert np.isclose(pm.sizing_equity(), 10000.0 - 100.0 - fee)                      # 浮虧要扣
    assert pm.open_notional("BBBUSDT", 1.0, pd.Timestamp("2026-01-02"), 0.10, 0.20)
    assert np.isclose(pm.positions_df.iloc[-1]["units"], (10000.0 - 100.0 - fee) * 0.10 / 1.0)
    k = pd.Series({"Datetime": pd.Timestamp("2026-01-03"), "High": 2.5, "Low": 2.3, "Close": 2.4})
    pm.update_positions({"AAAUSDT": k})
    assert pm.sizing_equity() == pm.paper_equity()                                      # 浮盈不加碼


def test_close_all_closes_every_position_with_reason(tmp_path):
    pm, _ = _pm(tmp_path)
    ts = pd.Timestamp("2026-01-01")
    pm.open_notional("AAAUSDT", 2.0, ts, 0.10, 0.20)
    pm.open_notional("BBBUSDT", 1.0, ts, 0.10, 0.20)
    closed = pm.close_all({"AAAUSDT": 2.2}, pd.Timestamp("2026-01-02"), "GATE_OFF_BTC")
    assert sorted(closed) == ["AAAUSDT", "BBBUSDT"] and pm.get_open_positions_symbols() == []
    rows = pm.positions_df.set_index("symbol")
    assert (rows["exit_reason"] == "GATE_OFF_BTC").all()
    assert rows.loc["AAAUSDT", "pnl"] > 0 and rows.loc["BBBUSDT", "pnl"] < 0          # BBB 沒價格 → 用進場價，只剩手續費
