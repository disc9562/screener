from dataclasses import dataclass

import numpy as np

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "8h": 480, "1d": 1440}


@dataclass(frozen=True)
class Bars:
    t: np.ndarray   # int64, open time ms
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    qv: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    def window(self, a: int, b: int) -> "Bars":
        return Bars(self.t[a:b], self.o[a:b], self.h[a:b], self.l[a:b],
                    self.c[a:b], self.v[a:b], self.qv[a:b])


def resample(b: Bars, minutes: int) -> Bars:
    step = minutes * MIN_MS
    keys = b.t // step * step
    uniq, first = np.unique(keys, return_index=True)
    last = np.r_[first[1:] - 1, len(keys) - 1]
    return Bars(
        uniq.astype(np.int64),
        b.o[first],
        np.maximum.reduceat(b.h, first),
        np.minimum.reduceat(b.l, first),
        b.c[last],
        np.add.reduceat(b.v, first),
        np.add.reduceat(b.qv, first),
    )
