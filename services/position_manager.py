import logging
import os

import pandas as pd

from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

POSITION_COLUMNS = [
    'symbol', 'status', 'entry_price', 'stop_loss', 'take_profit',
    'units', 'entry_timestamp', 'exit_timestamp', 'exit_reason', 'pnl',
    'entry_order_id', 'entry_fill_price',
]


class PositionManager:
    def __init__(self, notification_service: NotificationService, strategy_config: dict,
                 csv_path='data/positions.csv', order_execution_service=None):
        self.csv_path = csv_path
        self.notification_service = notification_service
        self.strategy_config = strategy_config
        self.order_execution_service = order_execution_service
        self.positions_df = self._load_positions()

    def _load_positions(self) -> pd.DataFrame:
        """Loads positions from the CSV file or creates an empty DataFrame."""
        if os.path.exists(self.csv_path):
            try:
                # 文字／時間欄固定讀成 object：pandas 3 會把全空欄推成 float64、把有值的推成 str，
                # 之後寫入 exit_reason / exit_timestamp 會丟 TypeError（重啟後無法平倉）
                df = pd.read_csv(self.csv_path, dtype={c: object for c in (
                    'symbol', 'status', 'entry_timestamp', 'exit_timestamp', 'exit_reason', 'entry_order_id')})
                for col in POSITION_COLUMNS:
                    if col not in df.columns:
                        df[col] = None
                return df
            except pd.errors.EmptyDataError:
                return self._create_empty_positions_df()
        return self._create_empty_positions_df()

    def _create_empty_positions_df(self) -> pd.DataFrame:
        """Creates an empty DataFrame with the required position columns."""
        df = pd.DataFrame(columns=POSITION_COLUMNS)
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        df.to_csv(self.csv_path, index=False)
        return df

    def _save_positions(self):
        """Saves the current positions DataFrame to the CSV file."""
        self.positions_df.to_csv(self.csv_path, index=False)

    def _opening_fee(self, entry_price: float, units: float) -> float:
        """Calculate the transaction fee for opening a position."""
        return entry_price * units * self.strategy_config.get('TRANSACTION_FEE_PERCENT')

    def _is_volume_on(self) -> bool:
        return self.strategy_config.get('use_volume_condition', False)

    def get_open_positions_symbols(self):
        """Returns a list of symbols for all positions with 'OPEN' status."""
        if self.positions_df.empty or 'status' not in self.positions_df.columns:
            return []
        open_positions = self.positions_df[self.positions_df['status'] == 'OPEN']
        return open_positions['symbol'].unique().tolist()

    def open_position(self, symbol: str, signal: dict):
        """Opens a new position based on a signal, if no open position exists for the symbol."""
        open_positions = self.positions_df[
            (self.positions_df['symbol'] == symbol) &
            (self.positions_df['status'] == 'OPEN')
        ]

        if not open_positions.empty:
            logger.info(f"Position for {symbol} is already open. Ignoring new BUY signal.")
            return False

        entry_price = signal.get('entry_price')
        stop_loss = signal.get('stop_loss')
        take_profit = signal.get('take_profit')
        timestamp = signal.get('timestamp')

        if entry_price is None or stop_loss is None:
            logger.error(f"Cannot open position for {symbol}: entry_price or stop_loss is missing from signal.")
            return False

        price_diff = abs(entry_price - stop_loss)
        if price_diff == 0:
            logger.error(f"Cannot open position for {symbol}: entry_price and stop_loss are the same.")
            return False

        total_capital = self.strategy_config.get('TOTAL_CAPITAL')
        risk_per_trade_percent = self.strategy_config.get('RISK_PER_TRADE_PERCENT')
        units = (total_capital * risk_per_trade_percent) / price_diff

        if units <= 0:
            logger.error(f"Calculated units for {symbol} is {units}. Must be positive.")
            return False

        new_position = {
            'symbol': symbol,
            'status': 'OPEN',
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'units': units,
            'entry_timestamp': timestamp,
            'exit_timestamp': pd.NaT,
            'exit_reason': None,
            'pnl': -self._opening_fee(entry_price, units),
        }

        new_pos_df = pd.DataFrame([new_position])
        self.positions_df = pd.concat([self.positions_df, new_pos_df], ignore_index=True)
        self._save_positions()
        logger.info(f"Opened new position for {symbol} at {entry_price}.")

        testnet_orders = self._execute_open_orders(symbol, units, stop_loss, take_profit)

        self.notification_service.send_trade_notification(
            symbol=symbol,
            action="BUY",
            price=entry_price,
            units=units,
            stop_loss_price=stop_loss,
            is_volume_on=self._is_volume_on(),
            testnet_orders=testnet_orders,
        )
        return True

    def _execute_open_orders(self, symbol: str, units: float,
                             stop_loss: float, take_profit: float) -> dict | None:
        """Place entry, SL, and TP orders on testnet. Returns order details or None."""
        if not self.order_execution_service:
            return None
        try:
            entry_order = self.order_execution_service.place_market_order(symbol, 'BUY', units)
            if not entry_order:
                logger.warning(f"Testnet market order failed for {symbol}, skipping SL/TP orders")
                return None
            sl_order = self.order_execution_service.place_stop_loss_order(symbol, 'SELL', units, stop_loss)
            tp_order = self.order_execution_service.place_take_profit_order(symbol, 'SELL', units, take_profit)
            return {'entry': entry_order, 'stop_loss': sl_order, 'take_profit': tp_order}
        except Exception as e:
            logger.error(f"Testnet order execution error for {symbol}: {e}")
            return None

    def _execute_close_orders(self, symbol: str, units: float) -> dict | None:
        """Close position on testnet. Returns order details or None."""
        if not self.order_execution_service:
            return None
        try:
            close_order = self.order_execution_service.close_position(symbol, units)
            if close_order:
                return {'close': close_order}
        except Exception as e:
            logger.error(f"Testnet close position error for {symbol}: {e}")
        return None

    def update_positions(self, latest_klines: dict):
        """Updates all open positions based on the latest kline data."""
        if self.positions_df.empty:
            return

        open_positions_indices = self.positions_df[self.positions_df['status'] == 'OPEN'].index
        if open_positions_indices.empty:
            return

        positions_closed = False
        for index in open_positions_indices:
            position = self.positions_df.loc[index]
            symbol = position['symbol']

            if symbol not in latest_klines:
                continue

            kline = latest_klines[symbol]
            exit_price = None
            exit_reason = None

            if kline['High'] >= position['take_profit']:
                exit_price = position['take_profit']
                exit_reason = 'TAKE_PROFIT'
            elif kline['Low'] <= position['stop_loss']:
                exit_price = position['stop_loss']
                exit_reason = 'STOP_LOSS'

            if exit_price is not None:
                fee_rate = self.strategy_config.get('TRANSACTION_FEE_PERCENT')
                closing_fee = exit_price * position['units'] * fee_rate
                pnl = (exit_price - position['entry_price']) * position['units'] - closing_fee

                self.positions_df.loc[index, 'status'] = 'CLOSED'
                self.positions_df.loc[index, 'exit_reason'] = exit_reason
                self.positions_df.loc[index, 'exit_timestamp'] = kline['Datetime']
                self.positions_df.loc[index, 'pnl'] = pnl
                logger.info(f"Closed position for {symbol} by {exit_reason}. PnL: {pnl:.2f}")
                positions_closed = True

                testnet_orders = self._execute_close_orders(symbol, position['units'])

                self.notification_service.send_trade_notification(
                    symbol=symbol,
                    action="SELL",
                    price=exit_price,
                    units=position['units'],
                    reason=exit_reason,
                    pnl=pnl,
                    is_volume_on=self._is_volume_on(),
                    testnet_orders=testnet_orders,
                )
            else:
                floating_pnl = (kline['Close'] - position['entry_price']) * position['units']
                opening_fee = self._opening_fee(position['entry_price'], position['units'])
                self.positions_df.loc[index, 'pnl'] = floating_pnl - opening_fee

        if positions_closed:
            self._save_positions()

    # --- Shotgun v3 ---
    def paper_equity(self) -> float:
        closed = self.positions_df[self.positions_df['status'] == 'CLOSED'] if not self.positions_df.empty else self.positions_df
        realized = float(pd.to_numeric(closed['pnl'], errors='coerce').fillna(0).sum()) if len(closed) else 0.0
        return float(self.strategy_config.get('TOTAL_CAPITAL')) + realized

    def open_notional(self, symbol, price, timestamp, notional_pct, disaster_stop_pct) -> bool:
        if symbol in self.get_open_positions_symbols():
            logger.info(f"Position for {symbol} is already open. Ignoring BUY.")
            return False
        units = self.paper_equity() * notional_pct / price
        stop = price * (1 - disaster_stop_pct)
        row = {'symbol': symbol, 'status': 'OPEN', 'entry_price': price, 'stop_loss': stop,
               'take_profit': float('nan'), 'units': units, 'entry_timestamp': timestamp,
               'exit_timestamp': pd.NaT, 'exit_reason': None, 'pnl': -self._opening_fee(price, units),
               'entry_order_id': None, 'entry_fill_price': float('nan')}
        self.positions_df = pd.concat([self.positions_df, pd.DataFrame([row])], ignore_index=True)
        self._save_positions()
        idx = self.positions_df.index[-1]
        info, orders = {"exchange": "disabled", "order_id": None, "fill_price": None}, None
        if self.order_execution_service:
            entry = None
            try:
                self.order_execution_service.ensure_cross_margin(symbol)
                entry = self.order_execution_service.place_market_order(symbol, 'BUY', units)
            except Exception as e:
                logger.error(f"Testnet entry order error for {symbol}: {e}")
            if not entry:
                # 交易所沒有這個倉 → 紙上也不留（否則之後的平倉會對一個不存在的部位下單）；下一根若條件仍成立會再試
                self.positions_df = self.positions_df.drop(index=idx)
                self._save_positions()
                self.notification_service.send_heartbeat_notification(
                    message=f"⚠️ {symbol} 交易所進場失敗，紙上不建倉")
                return False
            fill = float(entry.get('avgPrice') or 0) or None
            info = {"exchange": "filled", "order_id": entry.get('orderId'), "fill_price": fill}
            self.positions_df.loc[idx, ['entry_order_id', 'entry_fill_price']] = [
                None if info["order_id"] is None else str(info["order_id"]), fill if fill else float('nan')]
            self._save_positions()
            sl = None
            try:
                for _ in range(3):
                    sl = self.order_execution_service.place_stop_loss_order(symbol, 'SELL', units, stop)
                    if sl:
                        break
            except Exception as e:
                logger.error(f"Testnet stop order error for {symbol}: {e}")
            if not sl:
                self.notification_service.send_heartbeat_notification(
                    message=f"⚠️ {symbol} 已進場但災難止損單掛不上（試 3 次），部位沒有交易所保護，請手動處理")
            orders = {'entry': entry, 'stop_loss': sl}
        self.notification_service.send_trade_notification(
            symbol=symbol, action="BUY", price=price, units=units, stop_loss_price=stop,
            is_volume_on=False, testnet_orders=orders)
        return info

    def close_by_signal(self, symbol, price, timestamp, reason="CROSS_10_20") -> bool:
        idx = self.positions_df[(self.positions_df['symbol'] == symbol) & (self.positions_df['status'] == 'OPEN')].index
        if idx.empty:
            return False
        i = idx[0]
        pos = self.positions_df.loc[i]
        fee = self.strategy_config.get('TRANSACTION_FEE_PERCENT')
        pnl = (price - pos['entry_price']) * pos['units'] - self._opening_fee(pos['entry_price'], pos['units']) \
            - price * pos['units'] * fee
        self.positions_df.loc[i, ['status', 'exit_reason', 'exit_timestamp', 'pnl']] = ['CLOSED', reason, timestamp, pnl]
        self._save_positions()
        orders = self._execute_close_orders(symbol, pos['units'])
        if self.order_execution_service and not orders:
            self.notification_service.send_heartbeat_notification(
                message=f"⚠️ {symbol} 交易所平倉失敗（試 3 次），紙上已記為平倉，交易所多單與止損單仍在，請手動處理")
        self.notification_service.send_trade_notification(
            symbol=symbol, action="SELL", price=price, units=pos['units'], reason=reason, pnl=pnl,
            is_volume_on=False, testnet_orders=orders)
        return True
