import logging
import pandas as pd
from strategy.base import Strategy

class AlligatorStrategy:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.smma_values = {}
        self.volume_avg = {}  # Track average volume per symbol
        self.periods = [10, 20, 50, 233]
        self.volume_period = 20  # Period for volume moving average

    def warmup(self, symbol: str, df: pd.DataFrame):
        """Calculates initial SMMA values and average volume from a historical DataFrame."""
        if df.empty:
            self.logger.warning(f"Warmup for {symbol} failed: DataFrame is empty.")
            return

        self.smma_values[symbol] = {}
        for period in self.periods:
            # Calculate initial SMMA using EWM, then take the last value
            initial_smma = df['Close'].ewm(alpha=1/period, adjust=False).mean().iloc[-1]
            self.smma_values[symbol][f'smma{period}'] = initial_smma

        # Calculate initial average volume using SMA
        if 'Volume' in df.columns:
            initial_volume_avg = df['Volume'].tail(self.volume_period).mean()
            self.volume_avg[symbol] = initial_volume_avg
            self.logger.info(f"Warmup for {symbol} complete. Initial SMMA values: {self.smma_values[symbol]}, Volume avg: {initial_volume_avg:.2f}")
        else:
            self.logger.warning(f"No Volume column in DataFrame for {symbol}, volume condition will be disabled.")
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

        # Statefully update average volume using Simple Moving Average
        if symbol in self.volume_avg:
            prev_volume_avg = self.volume_avg[symbol]
            # Update using SMA formula: new_avg = prev_avg + (new_value - prev_avg) / period
            new_volume_avg = prev_volume_avg + (volume - prev_volume_avg) / self.volume_period
            self.volume_avg[symbol] = new_volume_avg

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

        # Apply volume condition if enabled
        use_volume_condition = self.config.get('use_volume_condition', False)
        if use_volume_condition and symbol in self.volume_avg:
            volume_multiplier = self.config.get('volume_multiplier', 2.5)
            volume_condition = volume > (self.volume_avg[symbol] * volume_multiplier)
            long_condition = base_condition and volume_condition
            if base_condition and not volume_condition:
                self.logger.debug(f"{symbol}: Base condition met but volume not high enough. Volume: {volume:.2f}, Avg: {self.volume_avg[symbol]:.2f}, Required: {self.volume_avg[symbol] * volume_multiplier:.2f}")
        else:
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
