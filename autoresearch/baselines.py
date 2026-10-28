import re
from pathlib import Path

BASELINES = ("rs_v1", "rs_v2", "alligator50", "alligator233", "martin30")
TEMPLATE = Path(__file__).with_name("baseline_candidate.py")


def variant_source(selection: str) -> str:
    """範本原始碼，SELECTION 與 DESCRIPTION 兩行換成指定的 baseline。"""
    if selection not in BASELINES:
        raise ValueError(f"unknown baseline: {selection}")
    src = TEMPLATE.read_text(encoding="utf-8")
    desc = f"baseline: {selection} + 現行 1h 進場 + 固定 20% 止損"
    src, n1 = re.subn(r'^SELECTION = ".*?"', f'SELECTION = "{selection}"', src, count=1, flags=re.M)
    src, n2 = re.subn(r'^DESCRIPTION = ".*?"', f'DESCRIPTION = "{desc}"', src, count=1, flags=re.M)
    assert n1 == n2 == 1, "template lost SELECTION/DESCRIPTION line"
    return src
