import math
from pathlib import Path

import pandas as pd
import pytest
import requests

from backend.data.market_history import (
    CSV_FIELDS,
    DownloadConfig,
    DownloadWindow,
    MarketHistoryError,
    MercadoBitcoinHistoryClient,
    inspect_chunk,
    iter_download_windows,
    merge_chunks_atomic,
    parse_candle_payload,
    write_chunk_atomic,
)
from backend.ml.chunked import build_dataset_from_chunks
from backend.ml.dataset import DatasetConfig, build_market_dataset, select_columns_for_export


def api_payload(timestamps: list[int], prices: list[float] | None = None) -> dict:
    prices = prices or [100.0 + index for index in range(len(timestamps))]
    return {
        "t": timestamps,
        "o": prices,
        "h": [price + 1.0 for price in prices],
        "l": [price - 1.0 for price in prices],
        "c": prices,
        "v": [10.0] * len(timestamps),
    }


def parsed_rows(start: int, count: int) -> list[dict]:
    timestamps = [start + index * 60 for index in range(count)]
    return parse_candle_payload(
        api_payload(timestamps),
        symbol="BTC-BRL",
        resolution="1m",
        start_timestamp=timestamps[0],
        end_timestamp=timestamps[-1],
    )


def test_download_windows_cover_range_without_overlap():
    windows = iter_download_windows(1_800_000_001, 1_801_814_401, chunk_days=7)

    assert windows[0].start_timestamp == 1_800_000_000
    assert windows[-1].end_timestamp == 1_801_814_400
    for previous, current in zip(windows, windows[1:]):
        assert previous.end_timestamp + 60 == current.start_timestamp


def test_payload_parser_rejects_mismatched_arrays():
    payload = api_payload([1_800_000_000])
    payload["v"] = []

    with pytest.raises(MarketHistoryError, match="different lengths"):
        parse_candle_payload(
            payload,
            symbol="BTC-BRL",
            resolution="1m",
            start_timestamp=1_800_000_000,
            end_timestamp=1_800_000_000,
        )


def test_payload_parser_rejects_invalid_ohlc_and_unsorted_timestamps():
    invalid = api_payload([1_800_000_000])
    invalid["h"] = [90.0]
    with pytest.raises(MarketHistoryError, match="inconsistent OHLC"):
        parse_candle_payload(
            invalid,
            symbol="BTC-BRL",
            resolution="1m",
            start_timestamp=1_800_000_000,
            end_timestamp=1_800_000_000,
        )

    with pytest.raises(MarketHistoryError, match="unsorted"):
        parse_candle_payload(
            api_payload([1_800_000_060, 1_800_000_000]),
            symbol="BTC-BRL",
            resolution="1m",
            start_timestamp=1_800_000_000,
            end_timestamp=1_800_000_060,
        )


def test_atomic_chunk_write_inspection_and_merge(tmp_path: Path):
    first = tmp_path / "1800000000_1800000180.csv"
    second = tmp_path / "1800000240_1800000360.csv"
    write_chunk_atomic(first, parsed_rows(1_800_000_000, 4))
    write_chunk_atomic(second, parsed_rows(1_800_000_240, 3))

    inspection = inspect_chunk(first, DownloadWindow(1_800_000_000, 1_800_000_180))
    merged = merge_chunks_atomic([second, first], tmp_path / "merged.csv")

    assert inspection["rows"] == 4
    assert merged["rows"] == 7
    assert merged["gap_buckets"]["1m"] == 6
    assert tuple(pd.read_csv(tmp_path / "merged.csv").columns) == CSV_FIELDS


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


class DiscoverySession:
    def __init__(self, first_timestamp: int):
        self.first_timestamp = first_timestamp
        self.calls = 0

    def get(self, *_args, **kwargs):
        self.calls += 1
        params = kwargs["params"]
        if params.get("countback") == 1:
            timestamp = min(self.first_timestamp, int(params["to"]))
            payload = api_payload([timestamp]) if int(params["to"]) >= self.first_timestamp else api_payload([])
            return FakeResponse(200, payload)
        start = int(params["from"])
        end = int(params["to"])
        timestamps = [self.first_timestamp] if start <= self.first_timestamp <= end else []
        return FakeResponse(200, api_payload(timestamps))


def test_client_retries_transient_failure_and_returns_valid_rows():
    start = 1_800_000_000
    session = FakeSession(
        [
            FakeResponse(429, {}),
            FakeResponse(200, api_payload([start, start + 60])),
        ]
    )
    sleeps = []
    clock = iter([0.0, 0.0, 2.0, 2.0])
    client = MercadoBitcoinHistoryClient(
        DownloadConfig(max_attempts=2),
        session=session,
        sleep=sleeps.append,
        monotonic=lambda: next(clock),
    )

    rows = client.get_candles(DownloadWindow(start, start + 60))

    assert len(rows) == 2
    assert session.calls == 2
    assert sleeps


def test_client_discovers_first_available_minute_with_countback_binary_search():
    start = 1_700_006_400
    first = start + 4 * 86_400 + 12 * 60
    session = DiscoverySession(first)
    clock_value = [0.0]

    def clock():
        clock_value[0] += 2.0
        return clock_value[0]

    client = MercadoBitcoinHistoryClient(
        DownloadConfig(),
        session=session,
        sleep=lambda _seconds: None,
        monotonic=clock,
    )

    discovered = client.discover_earliest_timestamp(start, start + 10 * 86_400)

    assert discovered == first
    assert session.calls < 10


def synthetic_rows(count: int = 1_200) -> list[dict]:
    start = 1_800_000_000
    rows = []
    for index in range(count):
        close = 100.0 + index * 0.02 + math.sin(index / 11.0) * 0.2
        rows.append(
            {
                "timestamp": start + index * 60,
                "datetime_utc": "",
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 10.0 + index % 9,
                "symbol": "BTC-BRL",
                "resolution": "1m",
                "source": "test",
            }
        )
    return rows


def test_chunked_dataset_matches_monolithic_dataset(tmp_path: Path):
    rows = synthetic_rows()
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    for start_index in range(0, len(rows), 400):
        chunk = rows[start_index : start_index + 400]
        path = chunks_dir / f"{chunk[0]['timestamp']}_{chunk[-1]['timestamp']}.csv"
        write_chunk_atomic(path, chunk)

    config = DatasetConfig()
    output = tmp_path / "dataset.csv"
    summary = build_dataset_from_chunks(chunks_dir, output, config=config)
    chunked = pd.read_csv(output)
    source = pd.DataFrame(rows).loc[:, ["timestamp", "open", "high", "low", "close", "volume"]]
    monolithic = select_columns_for_export(build_market_dataset(source, config), config.horizons_minutes)

    assert summary["rows"] == len(monolithic)
    pd.testing.assert_series_equal(chunked["timestamp"], monolithic["timestamp"], check_names=False)
    pd.testing.assert_series_equal(
        chunked["label"], monolithic["label"], check_names=False, check_dtype=False
    )
    pd.testing.assert_series_equal(
        chunked["return_60m_pct"],
        monolithic["return_60m_pct"].reset_index(drop=True),
        check_names=False,
        check_exact=False,
        rtol=1e-12,
    )
