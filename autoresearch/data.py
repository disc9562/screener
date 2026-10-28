"""5m kline download, cache and frozen universe."""
import io
import json
import os
import subprocess
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from autoresearch.bars import MIN_MS, Bars
from tools import backtest_compare as B


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


START_MS = _ms(2021, 1, 1)
END_MS = _ms(2026, 10, 22)
FETCH_START_MS = _ms(2020, 11, 1)
BTC_FETCH_START_MS = _ms(2020, 1, 1)
BTC = "BTCUSDT"


def _default_cache_dir() -> Path:
    env = os.environ.get("AR_CACHE_DIR")
    if env:
        return Path(env)
    common = subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=Path(__file__).resolve().parent, text=True).strip()
    return Path(common).parent / "data" / "backtest_cache" / "autoresearch"


CACHE_DIR: Path = _default_cache_dir()

ZIP_URL = ("https://data.binance.vision/data/futures/um/monthly/klines/"
           "{sym}/5m/{sym}-5m-{y:04d}-{m:02d}.zip")


def _next_month(ms: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return _ms(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_starts(start_ms: int, end_ms: int) -> list[int]:
    d = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    cur = _ms(d.year, d.month, 1)
    if cur < start_ms:
        cur = _next_month(cur)
    out = []
    while cur < end_ms:
        out.append(cur)
        cur = _next_month(cur)
    return out


def parse_kline_csv(text: str) -> np.ndarray:
    rows = []
    for i, line in enumerate(text.splitlines()):
        parts = line.strip().split(",")
        if len(parts) < 8:
            continue
        if i == 0:
            try:
                float(parts[0])
            except ValueError:
                continue
        rows.append([float(parts[k]) for k in (0, 1, 2, 3, 4, 5, 7)])
    return np.array(rows, dtype=np.float64).reshape(-1, 7)


def fetch_month_zip(sym: str, month_ms: int) -> np.ndarray | None:
    d = datetime.fromtimestamp(month_ms / 1000, tz=timezone.utc)
    url = ZIP_URL.format(sym=sym, y=d.year, m=d.month)
    req = urllib.request.Request(url, headers={"User-Agent": "screener-backtest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read(zf.namelist()[0]).decode("utf-8")
    return parse_kline_csv(text)


def fetch_api(sym: str, start_ms: int, end_ms: int) -> np.ndarray:
    rows: list[list[float]] = []
    cur = start_ms
    while cur < end_ms:
        batch = B._http_get_json(
            "/fapi/v1/klines",
            {"symbol": sym, "interval": "5m", "limit": 1500, "startTime": cur}, 0.2)
        if not batch:
            break
        rows.extend([float(r[k]) for k in (0, 1, 2, 3, 4, 5, 7)] for r in batch)
        if len(batch) < 1500:
            break
        cur = int(batch[-1][0]) + 5 * MIN_MS
    arr = np.array(rows, dtype=np.float64).reshape(-1, 7)
    return arr[arr[:, 0] < end_ms]


def _path(sym: str) -> Path:
    return CACHE_DIR / "5m" / f"{sym}.npz"


def save_5m(sym: str, b: Bars) -> None:
    p = _path(sym)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, t=b.t, o=b.o, h=b.h, l=b.l, c=b.c, v=b.v, qv=b.qv)


def load_5m(sym: str) -> Bars | None:
    p = _path(sym)
    if not p.exists():
        return None
    with np.load(p) as z:
        return Bars(z["t"].astype(np.int64), z["o"], z["h"], z["l"], z["c"], z["v"], z["qv"])


def download_symbol(sym: str, start_ms: int, end_ms: int, onboard_ms: int) -> Bars:
    parts = []
    for m in month_starts(start_ms, end_ms):
        nxt = _next_month(m)
        if nxt <= onboard_ms:
            continue
        if nxt <= end_ms:
            rows = fetch_month_zip(sym, m)
            if rows is None:
                rows = fetch_api(sym, m, nxt)
        else:
            rows = fetch_api(sym, m, end_ms)
        parts.append(rows)
    allr = np.vstack(parts) if parts else np.empty((0, 7))
    _, idx = np.unique(allr[:, 0], return_index=True)   # sorted by t, deduped
    allr = allr[idx]
    allr = allr[(allr[:, 0] >= start_ms) & (allr[:, 0] < end_ms)]
    b = Bars(allr[:, 0].astype(np.int64), *(allr[:, k].copy() for k in range(1, 7)))
    save_5m(sym, b)
    return b


def freeze_universe(n: int = 60) -> list[str]:
    p = CACHE_DIR / "universe.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["symbols"]
    syms = B.get_liquid_universe(top_n=n, refresh=False)
    if len(syms) < n:
        raise RuntimeError(f"get_liquid_universe 只回 {len(syms)} 檔（要 {n}），不寫 universe.json；"
                           "檢查網路或 Binance API 後重跑")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"created": datetime.now(timezone.utc).isoformat(),
                             "symbols": syms}), encoding="utf-8")
    return syms


def _complete(sym: str) -> bool:
    b = load_5m(sym)
    return b is not None and len(b) > 0 and b.t[-1] >= END_MS - 5 * MIN_MS


def ensure_data() -> list[str]:
    syms = freeze_universe()
    todo = [s for s in dict.fromkeys(syms + [BTC]) if not _complete(s)]
    onboard: dict[str, int] = {}
    if todo:
        info = B._cached_json(
            "exchange_info", lambda: B._http_get_json("/fapi/v1/exchangeInfo", delay=0.1))
        rows = info.get("symbols", []) if isinstance(info, dict) else []
        onboard = {s["symbol"]: int(s.get("onboardDate", 0)) for s in rows}
    for i, s in enumerate(todo, 1):
        start = BTC_FETCH_START_MS if s == BTC else FETCH_START_MS
        b = download_symbol(s, start, END_MS, onboard.get(s, 0))
        print(f"[{i}/{len(todo)}] {s}: {len(b)} bars")
    print(f"{len(dict.fromkeys(syms + [BTC]))} symbols ready")
    return syms


def cache_ready() -> bool:
    """只看 universe.json 與每檔 npz 在不在，不載入。"""
    up = CACHE_DIR / "universe.json"
    if not up.exists():
        return False
    syms = json.loads(up.read_text(encoding="utf-8"))["symbols"]
    return all(_path(s).exists() for s in dict.fromkeys(syms + [BTC]))


def load_all() -> tuple[list[str], dict[str, Bars]]:
    """只讀快取，不連網、不凍結選股池；缺東西就 FileNotFoundError。"""
    up = CACHE_DIR / "universe.json"
    if not up.exists():
        raise FileNotFoundError(f"missing {up}")
    syms = json.loads(up.read_text(encoding="utf-8"))["symbols"]
    data, missing = {}, []
    for s in dict.fromkeys(syms + [BTC]):
        b = load_5m(s)
        if b is None:
            missing.append(s)
        else:
            data[s] = b
    if missing:
        raise FileNotFoundError(f"missing 5m cache for: {', '.join(missing)}")
    return syms, data


if __name__ == "__main__":
    ensure_data()
