"""RS 只抓算分需要的 K 棒（權重 2），且分數與抓 1500 根時完全相同。"""
import numpy as np
from unittest.mock import MagicMock

from data.fetcher import kline_weight
from strategy.relative_strength import RelativeStrengthStrategy


def _klines(n, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    t0 = 1_700_000_000_000
    return [[t0 + i * 900_000, c, c * 1.01, c * 0.99, c, 1000.0, 0, 0, 0, 0, 0, 0] for i, c in enumerate(close)]


def test_rs_requests_only_needed_bars_and_score_is_unchanged():
    full = _klines(1500)
    strat = RelativeStrengthStrategy({})
    strat.crypto_fetcher = MagicMock()
    strat.crypto_fetcher.fetch_klines.side_effect = lambda s, i, limit: full[-limit:]

    small = strat.run("BTCUSDT", "15m")
    limit = strat.crypto_fetcher.fetch_klines.call_args.args[2]
    assert kline_weight(limit) == 2

    strat.crypto_fetcher.fetch_klines.side_effect = lambda s, i, limit: full
    big = strat.run("BTCUSDT", "15m")
    assert small["signal"] == big["signal"] == "RS_SCORE"
    assert np.isclose(small["score"], big["score"], rtol=1e-12)
