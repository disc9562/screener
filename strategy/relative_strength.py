import logging
from strategy.base import Strategy
from data.fetcher import CryptoFetcher
from data.transformer import transform_crypto_data
from config import RS_CALC_DAYS, RS_TIME_INTERVAL, CURRENT_TIMEZONE, CRYPTO_SMA_PERIODS

class RelativeStrengthStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)

    def _fetch_data(self, symbol: str, timeframe: str):
        # crypto_relative_strength.py uses limit=1500 for klines
        return self._fetch_crypto_data(symbol, timeframe, limit=1500)

    def _transform_data(self, raw_data, symbol: str, timeframe: str):
        return self._transform_crypto_data_default(raw_data)

    def _analyze(self, df, symbol: str):
        if df.empty:
            return {"signal": "NO_DATA"}

        # From crypto_relative_strength.py: calc_total_bars
        # This needs to be passed from config or determined dynamically
        # For now, let's assume 'days' is part of the config or a default
        days = RS_CALC_DAYS
        time_interval = RS_TIME_INTERVAL

        bars = self._calc_total_bars(time_interval, days)
        if bars is None:
            return {"signal": "INVALID_TIME_INTERVAL"}

        if len(df) < bars + 60: # +60 for SMA calculation buffer
            return {"signal": "INSUFFICIENT_DATA_FOR_RS"}

        # Ensure SMAs are calculated (transformer should do this, but double check)
        for period in CRYPTO_SMA_PERIODS: # From base Strategy
            if f'SMA_{period}' not in df.columns:
                # This should ideally not happen if transformer is working correctly
                # But as a fallback, calculate here
                df[f'SMA_{period}'] = df['Close'].rolling(window=period).mean()
        
        # Drop NaNs after SMA calculation
        df = df.dropna()
        if df.empty:
            return {"signal": "INSUFFICIENT_DATA_AFTER_SMA_DROP"}

        rs_score = 0.0
        # The original loop starts from 1 to bars+1
        # df.values[-i] means the i-th element from the end
        # So, df.iloc[-i] is equivalent
        for i in range(1, bars + 1):
            current_close = df['Close'].iloc[-i]
            moving_average_30 = df['SMA_30'].iloc[-i]
            moving_average_45 = df['SMA_45'].iloc[-i]
            moving_average_60 = df['SMA_60'].iloc[-i]
            
            # Formula from crypto_relative_strength.py
            weight = (((current_close - moving_average_30) +
                       (current_close - moving_average_45) +
                       (current_close - moving_average_60)) *
                      (((bars - i) * days / bars) + 1) +
                      (moving_average_30 - moving_average_45) +
                      (moving_average_30 - moving_average_60) +
                      (moving_average_45 - moving_average_60)) / moving_average_60
            rs_score += weight * (bars - i)

        return {"signal": "RS_SCORE", "score": rs_score}

    def _calc_total_bars(self, time_interval, days):
        """Helper function from crypto_relative_strength.py"""
        bars_dict = {
            "5m": 12 * 24 * days,
            "15m": 4 * 24 * days,
            "30m": 2 * 24 * days,
            "1h":  24 * days,
            "2h": 12 * days,
            "4h": 6 * days,
        }
        return bars_dict.get(time_interval)
