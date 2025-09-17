import pandas as pd
import os
import logging
from services.notification_service import NotificationService

class PositionManager:
    def __init__(self, notification_service: NotificationService, strategy_config: dict, csv_path='data/positions.csv'):
        self.csv_path = csv_path
        self.notification_service = notification_service
        self.strategy_config = strategy_config # Store the strategy config
        self.positions_df = self._load_positions()
        

    def _load_positions(self) -> pd.DataFrame:
        """Loads positions from the CSV file or creates an empty DataFrame."""
        if os.path.exists(self.csv_path):
            try:
                df = pd.read_csv(self.csv_path)
                required_columns = [
                    'symbol', 'status', 'entry_price', 'stop_loss', 'take_profit', 
                    'units', 'entry_timestamp', 'exit_timestamp', 'exit_reason', 'pnl'
                ]
                for col in required_columns:
                    if col not in df.columns:
                        df[col] = None
                return df
            except pd.errors.EmptyDataError:
                return self._create_empty_positions_df()
        else:
            return self._create_empty_positions_df()

    def _create_empty_positions_df(self) -> pd.DataFrame:
        """Creates an empty DataFrame with the required position columns."""
        columns = [
            'symbol', 'status', 'entry_price', 'stop_loss', 'take_profit', 
            'units', 'entry_timestamp', 'exit_timestamp', 'exit_reason', 'pnl'
        ]
        df = pd.DataFrame(columns=columns)
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        df.to_csv(self.csv_path, index=False)
        return df

    def _save_positions(self):
        """Saves the current positions DataFrame to the CSV file."""
        self.positions_df.to_csv(self.csv_path, index=False)

    def get_open_positions_symbols(self):
        """
        Returns a list of symbols for all positions with 'OPEN' status.
        """
        if self.positions_df.empty or 'status' not in self.positions_df.columns:
            return []
        
        open_positions = self.positions_df[self.positions_df['status'] == 'OPEN']
        return open_positions['symbol'].unique().tolist()

    def open_position(self, symbol: str, signal: dict):
        """
        Opens a new position based on a signal, if no open position exists for the symbol.
        """
        open_positions = self.positions_df[
            (self.positions_df['symbol'] == symbol) &
            (self.positions_df['status'] == 'OPEN')
        ]

        if not open_positions.empty:
            logging.info(f"Position for {symbol} is already open. Ignoring new BUY signal.")
            return False

        entry_price = signal.get('entry_price')
        stop_loss = signal.get('stop_loss')
        take_profit = signal.get('take_profit')
        timestamp = signal.get('timestamp')

        # Calculate units based on risk management (AC2)
        total_capital = self.strategy_config.get('TOTAL_CAPITAL')
        risk_per_trade_percent = self.strategy_config.get('RISK_PER_TRADE_PERCENT')

        if entry_price is None or stop_loss is None:
            logging.error(f"Cannot open position for {symbol}: entry_price or stop_loss is missing from signal.")
            return False

        price_diff = abs(entry_price - stop_loss)
        if price_diff == 0:
            logging.error(f"Cannot open position for {symbol}: entry_price and stop_loss are the same. Units cannot be calculated.")
            return False

        # Calculate units
        risk_amount = total_capital * risk_per_trade_percent
        units = risk_amount / price_diff

        if units <= 0:
            logging.error(f"Calculated units for {symbol} is {units}. Must be positive. Aborting position opening.")
            return False

        new_position = {
            'symbol': symbol,
            'status': 'OPEN',
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'units': units, # Use calculated units
            'entry_timestamp': timestamp,
            'exit_timestamp': pd.NaT,
            'exit_reason': None,
            'pnl': -(entry_price * units * self.strategy_config.get('TRANSACTION_FEE_PERCENT')) # Account for opening fee
        }
        
        new_pos_df = pd.DataFrame([new_position])
        self.positions_df = pd.concat([self.positions_df, new_pos_df], ignore_index=True)
        
        self._save_positions()
        logging.info(f"Opened new position for {symbol} at {new_position['entry_price']}.")
        
        self.notification_service.send_trade_notification(
            symbol=symbol,
            action="BUY",
            price=new_position['entry_price'],
            units=new_position['units'],
            stop_loss_price=new_position['stop_loss']
        )
        return True

    def update_positions(self, latest_klines: dict):
        """
        Updates all open positions based on the latest kline data.
        """
        if self.positions_df.empty:
            return

        open_positions_indices = self.positions_df[self.positions_df['status'] == 'OPEN'].index
        if open_positions_indices.empty:
            return

        positions_updated = False
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
                self.positions_df.loc[index, 'status'] = 'CLOSED'
                self.positions_df.loc[index, 'exit_reason'] = exit_reason
                self.positions_df.loc[index, 'exit_timestamp'] = kline['Datetime']
                pnl = (exit_price - position['entry_price']) * position['units']
                # Account for closing fee
                closing_fee = exit_price * position['units'] * self.strategy_config.get('TRANSACTION_FEE_PERCENT')
                pnl -= closing_fee
                self.positions_df.loc[index, 'pnl'] = pnl
                logging.info(f"Closed position for {symbol} by {exit_reason}. PnL: {pnl:.2f}")
                positions_updated = True
                
                self.notification_service.send_trade_notification(
                    symbol=symbol,
                    action="SELL",
                    price=exit_price,
                    units=position['units'],
                    reason=exit_reason,
                    pnl=pnl
                )
            else:
                # Calculate floating PnL from price change
                floating_pnl_from_price_change = (kline['Close'] - position['entry_price']) * position['units']
                # The initial PnL already includes the opening fee (which is negative)
                # So, the current PnL is the floating PnL from price change plus the initial PnL (opening fee)
                self.positions_df.loc[index, 'pnl'] = floating_pnl_from_price_change + (-(position['entry_price'] * position['units'] * self.strategy_config.get('TRANSACTION_FEE_PERCENT')))

        if positions_updated:
            self._save_positions()
