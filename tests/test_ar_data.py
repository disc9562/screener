import numpy as np

from autoresearch import data
from autoresearch.bars import MIN_MS


def _line(t, k):
    return ",".join(str(x) for x in [t, 1, 2, 0.5, 1.5, 10, t + 299999, 100 + k, 5, 4, 50, 0])


def test_parse_kline_csv_with_and_without_header():
    body = _line(1000, 1) + "\n" + _line(1300, 2) + "\n"
    header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,tbv,tbqv,ignore\n"
    for text in (body, header + body):
        arr = data.parse_kline_csv(text)
        assert arr.shape == (2, 7) and arr[0, 6] == 101 and arr[1, 0] == 1300


def test_fetch_api_paginates_until_end(monkeypatch):
    calls = []
    t0 = 1_000_000_000_000
    step = 5 * MIN_MS

    def fake(path, params, delay):
        calls.append(params["startTime"])
        n = 1500 if len(calls) == 1 else 10
        s = params["startTime"]
        return [[s + i * step, 1, 2, 0.5, 1.5, 10, 0, 100] for i in range(n)]

    monkeypatch.setattr(data.B, "_http_get_json", fake)
    end = t0 + 1505 * step
    out = data.fetch_api("X", t0, end)
    assert len(calls) == 2 and calls[1] == t0 + 1499 * step + step
    assert out[:, 0].max() < end and len(out) == 1505


def test_download_symbol_skips_before_onboard_and_falls_back_on_404(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    ms = data._ms
    zips, apis = [], []

    def row(t):
        return [t, 1, 2, 0.5, 1.5, 10, 100]

    def fake_zip(sym, m):
        zips.append(m)
        return None if m == ms(2021, 4, 1) else np.array([row(m + 300_000), row(m)], float)

    def fake_api(sym, a, b):
        apis.append((a, b))
        return np.array([row(a), row(a + 300_000)], float)

    monkeypatch.setattr(data, "fetch_month_zip", fake_zip)
    monkeypatch.setattr(data, "fetch_api", fake_api)
    end = ms(2021, 6, 10)
    b = data.download_symbol("X", ms(2021, 1, 1), end, ms(2021, 3, 15))
    assert ms(2021, 1, 1) not in zips and ms(2021, 2, 1) not in zips
    assert (ms(2021, 4, 1), ms(2021, 5, 1)) in apis and (ms(2021, 6, 1), end) in apis
    loaded = data.load_5m("X")
    assert np.array_equal(loaded.t, b.t) and np.all(np.diff(b.t) > 0)


def test_freeze_universe_writes_once(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    n = []
    monkeypatch.setattr(data.B, "get_liquid_universe",
                        lambda top_n, refresh: n.append(1) or ["A", "B"])
    assert data.freeze_universe() == data.freeze_universe() == ["A", "B"]
    assert len(n) == 1
