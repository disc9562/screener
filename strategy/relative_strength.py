import logging
from strategy.base import Strategy
from data.fetcher import CryptoFetcher
from data.transformer import transform_crypto_data
from config import RS_CALC_DAYS, RS_TIME_INTERVAL, CURRENT_TIMEZONE, CRYPTO_SMA_PERIODS

class RelativeStrengthStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)

    def _fetch_data(self, symbol: str, timeframe: str):
        # Fetch only what the score needs: RS window + the "+60" check in _analyze + SMA60 warmup rows that
        # transform_crypto_data drops. 15m/3 days = 408 bars -> request weight 2 (1500 bars weighed 10).
        bars = self._calc_total_bars(RS_TIME_INTERVAL, RS_CALC_DAYS)
        limit = min(1500, bars + 2 * max(CRYPTO_SMA_PERIODS)) if bars else 1500
        return self._fetch_crypto_data(symbol, timeframe, limit=limit)

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

        c = df['Close'].to_numpy(dtype=float)[-bars:]
        a30 = df['SMA_30'].to_numpy(dtype=float)[-bars:]
        a45 = df['SMA_45'].to_numpy(dtype=float)[-bars:]
        a60 = df['SMA_60'].to_numpy(dtype=float)[-bars:]
        import numpy as np
        j = np.arange(bars, dtype=float)
        weight = (((c - a30) + (c - a45) + (c - a60)) * (((j * days) / bars) + 1)
                  + (a30 - a45) + (a30 - a60) + (a45 - a60)) / a60
        rs_score = float(np.sum(weight * j))

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
