import logging
import pandas as pd
from strategy.base import Strategy

class AlligatorStrategy:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.smma_values = {}
        self.periods = [10, 20, 50, 233]

    def warmup(self, symbol: str, df: pd.DataFrame):
        """Calculates initial SMMA values from a historical DataFrame."""
        if df.empty:
            self.logger.warning(f"Warmup for {symbol} failed: DataFrame is empty.")
            return

        self.smma_values[symbol] = {}
        for period in self.periods:
            # Calculate initial SMMA using EWM, then take the last value
            initial_smma = df['Close'].ewm(alpha=1/period, adjust=False).mean().iloc[-1]
            self.smma_values[symbol][f'smma{period}'] = initial_smma
        self.logger.info(f"Warmup for {symbol} complete. Initial SMMA values: {self.smma_values[symbol]}")

    def run_with_kline(self, symbol: str, kline: dict):
        """
        Runs analysis on a single incoming kline and returns a signal if conditions are met.
        This is the new stateful, streaming method.
        """
        if symbol not in self.smma_values:
            self.logger.warning(f"No SMMA values for {symbol}, skipping analysis. Ensure warmup is called first.")
            return None

        # Extract data from kline dictionary
        close_price = float(kline['c'])
        volume = float(kline['v'])
        timestamp = pd.to_datetime(kline['t'], unit='ms')

        # Statefully update SMMA values
        # new_smma = (previous_smma * (period - 1) + new_close) / period
        for period in self.periods:
            prev_smma = self.smma_values[symbol][f'smma{period}']
            new_smma = (prev_smma * (period - 1) + close_price) / period
            self.smma_values[symbol][f'smma{period}'] = new_smma

        # Check entry conditions using the newly calculated SMMA values
        smma10 = self.smma_values[symbol]['smma10']
        smma20 = self.smma_values[symbol]['smma20']
        smma50 = self.smma_values[symbol]['smma50']
        smma233 = self.smma_values[symbol]['smma233']

        base_condition = (
            smma10 > smma20 and
            smma20 > smma50 and
            smma50 > smma233 and
            close_price > smma233 and
            close_price > smma10
        )

        # Volume condition is not applicable here as we don't have previous volume easily
        # This simplification is acceptable for now to fix the core logic.
        long_condition = base_condition

        if long_condition:
            self.logger.info(f"Signal condition met for {symbol} at price {close_price}")
            equity = self.config.get('equity', 100000)
            risk_percent = self.config.get('risk_percent', 0.05)
            
            entry_price = close_price
            stop_loss_price = smma233
            
            if entry_price > stop_loss_price:
                offset = entry_price - stop_loss_price
                if offset == 0:
                    self.logger.warning(f"Offset is zero for {symbol}, cannot calculate units.")
                    return None
                
                units = (equity * risk_percent) / offset
                
                signal = {
                    'timestamp': timestamp,
                    'signal': 'BUY',
                    'entry_price': entry_price,
                    'stop_loss': stop_loss_price,
                    'take_profit': entry_price + 20 * offset,
                    'units': units
                }
                return signal
        
        return None
