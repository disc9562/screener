import logging
import os

import pandas as pd

from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

POSITION_COLUMNS = [
    'symbol', 'status', 'entry_price', 'stop_loss', 'take_profit',
    'units', 'entry_timestamp', 'exit_timestamp', 'exit_reason', 'pnl'
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
                df = pd.read_csv(self.csv_path)
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
