import logging
from strategy.base import Strategy
import pandas as pd

class AlligatorStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)

    def run(self, symbol: str, timeframe: str = "15m"):
        """Orchestrates the data fetching, analysis, and signal generation."""
        return super().run(symbol, timeframe)

    def run_with_existing_data(self, df: pd.DataFrame):
        """
        Runs the analysis on an existing DataFrame without fetching new data.
        """
        if df is None or df.empty:
            print("DataFrame is empty. Cannot run analysis.")
            return []
        
        
        signals = self._analyze(df)
        
        return signals

    def _fetch_data(self, symbol: str, timeframe: str):
        """
        Fetches data for the Alligator Strategy.
        For crypto, we will use the 15m timeframe.
        """
        if timeframe != "15m":
            print(f"Warning: AlligatorStrategy is designed for 15m timeframe, but {timeframe} was requested.")
        
        # Using the helper from the base class
        return self._fetch_crypto_data(symbol, interval=timeframe)

    def _transform_data(self, raw_data, symbol: str, timeframe: str) -> pd.DataFrame:
        """
        Transforms raw kline data into a pandas DataFrame.
        """
        # Using the helper from the base class
        return self._transform_crypto_data_default(raw_data)

    def _analyze(self, df: pd.DataFrame):
        """
        Analyzes the data and generates a list of trading signals.
        """
        print("Analyzing data for Alligator Strategy...")
        

        # SMMA Calculation
        for period in [10, 20, 50, 233]:
            df[f'smma{period}'] = df['Close'].ewm(alpha=1/period, adjust=False).mean()
        
        

        # Entry Conditions
        base_condition = (
            (df['smma10'] > df['smma20']) &
            (df['smma20'] > df['smma50']) &
            (df['smma50'] > df['smma233']) &
            (df['Close'] > df['smma233']) &
            (df['Close'] > df['smma10'])
        )
        use_volume_condition = self.config.get('use_volume_condition', True)
        if self.config.get('local_test_mode'): # Bypass volume condition in local test mode
            use_volume_condition = False
        long_condition = base_condition
        if use_volume_condition:
            volume_multiplier = self.config.get('volume_multiplier', 2.5)
            volume_condition = df['Volume'] > (df['Volume'].shift(1) * volume_multiplier)
            # Temporarily bypass volume condition for testing
            # long_condition = base_condition & volume_condition
            long_condition = base_condition
        
        
        
        if use_volume_condition:
            pass

        # Find entry points
        entry_points = df[long_condition]
        
        
        
        
        signals = []
        if not entry_points.empty:
            # For simplicity in this story, we only act on the most recent signal.
            # Story 5 will introduce state management for handling existing positions.
            last_entry = entry_points.iloc[-1]
            
            # Task 4: Position Sizing
            equity = self.config.get('equity', 100000) # Default equity, should be configurable
            risk_percent = self.config.get('risk_percent', 0.05) # Default risk percent
            
            entry_price = last_entry['Close']
            stop_loss_price = last_entry['smma233']
            
            if entry_price > stop_loss_price: # Check if it's a valid long signal
                offset = entry_price - stop_loss_price
                units = (equity * risk_percent) / offset
                
                signal = {
                    'timestamp': last_entry.name, # Assuming index is timestamp
                    'signal': 'BUY',
                    'entry_price': entry_price,
                    'stop_loss': stop_loss_price,
                    'take_profit': entry_price + 20 * offset,
                    'units': units
                }
                signals.append(signal)
            else:
                pass

        print(f"Generated {len(signals)} signals.")
        return signals