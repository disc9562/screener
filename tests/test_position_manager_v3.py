from unittest.mock import MagicMock

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
    return svc


def test_cancel_open_orders_also_cancels_conditional_algo_orders():
    """C3：STOP_MARKET 會被轉送到 algo 端點，撤單必須也帶 conditional=True。"""
    svc = _svc()
    svc.cancel_open_orders("AAAUSDT")
    calls = [c.kwargs for c in svc.client.futures_cancel_all_open_orders.call_args_list]
    assert {"symbol": "AAAUSDT"} in calls and {"symbol": "AAAUSDT", "conditional": True} in calls
