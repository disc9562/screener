import pytest

from autoresearch.baselines import BASELINES, TEMPLATE, variant_source
from autoresearch.guard import check_candidate_source

HEAD = "import numpy as np\nimport pandas as pd\nimport config\nfrom strategy import shotgun_v3 as V\n"

ATR_CANDIDATE = '''
import math

import numpy as np
import pandas as pd

from autoresearch.bars import DAY_MS, resample
from autoresearch.indicators import rma, rma_last
from config import DISASTER_STOP_PCT
from strategy import shotgun_v3 as V

DESCRIPTION = "ATR 止損 + BTC 4h 趨勢過濾"
ENTRY_TF = "4h"
SELECT_WINDOW_4H = 300
TOP_N = 20
CACHE = {}


def _atr(b, n=14):
    prev = np.r_[b.c[0], b.c[:-1]]
    tr = np.maximum(b.h - b.l, np.maximum(np.abs(b.h - prev), np.abs(b.l - prev)))
    return rma(tr, n)


def select_score(w, cut_ms, btc_w):
    if len(w) < 60 or len(btc_w) < 60:
        return None
    return V.rs_score(w.c, len(w) - 1)


def btc_gate(btc_close):
    return len(btc_close) >= 250 and V.btc_bull(btc_close)


def signals(b, aux):
    btc = resample(aux["btc5m"], 240)
    k = np.searchsorted(btc.t + 4 * 3_600_000, b.t + 4 * 3_600_000, "right") - 1
    btc_up = np.where(k >= 0, btc.c[np.maximum(k, 0)], np.nan) > rma(btc.c, 50)[np.maximum(k, 0)]
    atr = _atr(b)
    s20 = pd.Series(b.c).rolling(20).mean().to_numpy()
    with np.errstate(invalid="ignore"):
        entry = (b.c > s20) & btc_up & np.isfinite(atr)
    stop = b.c - 2.5 * atr
    CACHE["n"] = len(b)
    return {"entry": entry, "stop": stop, "trail": b.c - 3 * atr}


def update_stop(sig, i, pos):
    t = float(sig["trail"][i])
    return t if math.isfinite(t) else pos.stop
'''

FORBIDDEN = [
    "import os",
    "import autoresearch.engine as E",
    "from autoresearch import data",
    "from autoresearch import engine",
    "from . import helper",
    "import strategy.shotgun_v3",
    "from numpy import *",
    "from config import os",
    "f = open('x')",
    "exec('1')",
    "eval('1')",
    "compile('1', 'f', 'exec')",
    "__import__('os')",
    "g = globals()",
    "g = locals()",
    "g = vars(np)",
    "setattr(np, 'max', len)",
    "delattr(np, 'max')",
    "m = getattr(np, 'max')",
    "x = input()",
    "breakpoint()",
    "d = np.__dict__",
    "class A:\n    def __init__(self):\n        pass",
    "def f(b):\n    b.c.setflags(write=True)",
    "def f(b):\n    return b.c.flags",
    "def f(b):\n    return b.c.base",
    "def f(b):\n    return b.c.ctypes",
    "np.foo = 1",
    "V.ER_MIN = 0.1",
    "np.e += 1",
    "del np.e",
    "m = np\nm.max = len",
    "x = np.load('BTCUSDT.npz')",
    "x = pd.read_csv('x.csv')",
    "config.os.system('true')",
]


def _check(tmp_path, src):
    p = tmp_path / "candidate.py"
    p.write_text(src, encoding="utf-8")
    return check_candidate_source(p)


def test_baselines_and_atr_candidate_pass(tmp_path):
    assert check_candidate_source(TEMPLATE) is None
    for sel in BASELINES:
        assert _check(tmp_path, variant_source(sel)) is None
    assert _check(tmp_path, ATR_CANDIDATE) is None


@pytest.mark.parametrize("snippet", FORBIDDEN)
def test_forbidden_forms_rejected(tmp_path, snippet):
    got = _check(tmp_path, HEAD + snippet + "\n")
    assert got is not None and got.startswith("forbidden: ") and " at line " in got
    assert int(got.rsplit(" ", 1)[1]) > HEAD.count("\n")  # 指到 snippet，不是開頭的合法 import
