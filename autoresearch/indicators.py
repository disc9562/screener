import numpy as np
import pandas as pd


def rma(x, n):
    # ponytail: pandas ewm(adjust=False, alpha=1/n) 就是 Pine rma 的遞迴，只差種子；把種子放進第一格。
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    seeded = np.r_[x[:n].mean(), x[n:]]
    out[n - 1:] = pd.Series(seeded).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    return out


def rma_last(x, n):
    # 只要最後一個值：a^m·seed + Σ (1−a)·a^(m−j)·x_j，O(len) 一次內積。選標的每天要呼叫十幾萬次，不能走 pandas。
    a = (n - 1) / n
    m = len(x) - n
    if m < 0:
        return float("nan")
    w = (1 - a) * a ** np.arange(m - 1, -1, -1)
    return float(a ** m * np.mean(x[:n]) + w @ np.asarray(x[n:], float))
