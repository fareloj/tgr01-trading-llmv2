from __future__ import annotations

import csv
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


MB_CANDLES_URL = "https://api.mercadobitcoin.net/api/v4/candles"
CSV_FIELDS = (
    "timestamp",
    "datetime_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
    "resolution",
    "source",
)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class MarketHistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadWindow:
    start_timestamp: int
    end_timestamp: int

    @property
    def filename(self) -> str:
        return f"{self.start_timestamp}_{self.end_timestamp}.csv"


@dataclass(frozen=True)
class DownloadConfig:
    symbol: str = "BTC-BRL"
    resolution: str = "1m"
    chunk_days: int = 7
    minimum_request_interval_seconds: float = 1.05
    timeout_seconds: float = 30.0
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.resolution != "1m":
            raise ValueError("bulk dataset downloader currently supports only 1m candles")
        if self.chunk_days <= 0:
            raise ValueError("chunk_days must be positive")
        if self.minimum_request_interval_seconds < 1.0:
            raise ValueError("Mercado Bitcoin candles rate limit requires at least one second between requests")
        if self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ValueError("timeout and max_attempts must be positive")


def align_minute(timestamp: int) -> int:
    return int(timestamp) - (int(timestamp) % 60)


def iter_download_windows(start_timestamp: int, end_timestamp: int, chunk_days: int) -> list[DownloadWindow]:
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    start = align_minute(start_timestamp)
    end = align_minute(end_timestamp)
    if end < start:
        raise ValueError("end timestamp must not be earlier than start timestamp")

    chunk_minutes = chunk_days * 24 * 60
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + (chunk_minutes - 1) * 60, end)
        windows.append(DownloadWindow(cursor, window_end))
        cursor = window_end + 60
    return windows


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketHistoryError(f"invalid numeric value in {field}") from exc
    if not math.isfinite(result):
        raise MarketHistoryError(f"non-finite numeric value in {field}")
    return result


def parse_candle_payload(
    payload: Any,
    *,
    symbol: str,
    resolution: str,
    start_timestamp: int,
    end_timestamp: int,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise MarketHistoryError("candles response must be a JSON object")
    required = ("t", "o", "h", "l", "c", "v")
    arrays = []
    for key in required:
        value = payload.get(key)
        if not isinstance(value, list):
            raise MarketHistoryError(f"candles response field {key} must be an array")
        arrays.append(value)
    lengths = {len(value) for value in arrays}
    if len(lengths) != 1:
        raise MarketHistoryError("candles response arrays have different lengths")

    rows = []
    previous_timestamp = None
    for timestamp_raw, open_raw, high_raw, low_raw, close_raw, volume_raw in zip(*arrays):
        try:
            timestamp = int(timestamp_raw)
        except (TypeError, ValueError) as exc:
            raise MarketHistoryError("invalid candle timestamp") from exc
        if timestamp < start_timestamp or timestamp > end_timestamp:
            raise MarketHistoryError("API returned a candle outside the requested window")
        if timestamp % 60 != 0:
            raise MarketHistoryError("API returned a candle outside the one-minute grid")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise MarketHistoryError("API returned duplicate or unsorted timestamps")

        open_price = _number(open_raw, "open")
        high = _number(high_raw, "high")
        low = _number(low_raw, "low")
        close = _number(close_raw, "close")
        volume = _number(volume_raw, "volume")
        if min(open_price, high, low, close) <= 0 or volume < 0:
            raise MarketHistoryError("API returned non-positive prices or negative volume")
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise MarketHistoryError("API returned inconsistent OHLC ranges")

        rows.append(
            {
                "timestamp": timestamp,
                "datetime_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "symbol": symbol,
                "resolution": resolution,
                "source": "mercado_bitcoin_api_v4",
            }
        )
        previous_timestamp = timestamp
    return rows


class MercadoBitcoinHistoryClient:
    def __init__(
        self,
        config: DownloadConfig | None = None,
        *,
        session: requests.Session | None = None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        self.config = config or DownloadConfig()
        self.session = session or requests.Session()
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_started: float | None = None

    def _throttle(self) -> None:
        now = self._monotonic()
        if self._last_request_started is not None:
            remaining = self.config.minimum_request_interval_seconds - (now - self._last_request_started)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_started = self._monotonic()

    def _get_json(self, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_attempts):
            self._throttle()
            try:
                response = self.session.get(MB_CANDLES_URL, params=params, timeout=self.config.timeout_seconds)
                if response.status_code in TRANSIENT_STATUS_CODES:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 30)
                    last_error = MarketHistoryError(f"transient HTTP {response.status_code}")
                    if attempt + 1 < self.config.max_attempts:
                        self._sleep(wait)
                        continue
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError, MarketHistoryError) as exc:
                last_error = exc
                if attempt + 1 < self.config.max_attempts:
                    self._sleep(min(2 ** attempt, 30))
                    continue
                break
        raise MarketHistoryError(f"Mercado Bitcoin request failed after retries: {last_error}") from last_error

    def get_candles(self, window: DownloadWindow) -> list[dict[str, Any]]:
        payload = self._get_json(
            {
                "symbol": self.config.symbol,
                "resolution": self.config.resolution,
                "from": window.start_timestamp,
                "to": window.end_timestamp,
            }
        )
        return parse_candle_payload(
            payload,
            symbol=self.config.symbol,
            resolution=self.config.resolution,
            start_timestamp=window.start_timestamp,
            end_timestamp=window.end_timestamp,
        )

    def discover_earliest_timestamp(self, search_start_timestamp: int, end_timestamp: int) -> int:
        start_day = int(search_start_timestamp) // 86_400
        end_day = int(end_timestamp) // 86_400
        if end_day < start_day:
            raise ValueError("discovery end must not be earlier than start")

        def has_minute_candle(day: int) -> bool:
            payload = self._get_json(
                {
                    "symbol": self.config.symbol,
                    "resolution": self.config.resolution,
                    "to": (day + 1) * 86_400 - 60,
                    "countback": 1,
                }
            )
            timestamps = payload.get("t") if isinstance(payload, dict) else None
            if not isinstance(timestamps, list):
                raise MarketHistoryError("countback discovery returned an invalid payload")
            return bool(timestamps)

        if not has_minute_candle(end_day):
            raise MarketHistoryError("no one-minute candles are available before the requested end")
        low = start_day
        high = end_day
        while low < high:
            middle = (low + high) // 2
            if has_minute_candle(middle):
                high = middle
            else:
                low = middle + 1

        day_start = low * 86_400
        day_end = day_start + 86_400 - 60
        payload = self._get_json(
            {
                "symbol": self.config.symbol,
                "resolution": self.config.resolution,
                "from": day_start,
                "to": day_end,
            }
        )
        rows = parse_candle_payload(
            payload,
            symbol=self.config.symbol,
            resolution=self.config.resolution,
            start_timestamp=day_start,
            end_timestamp=day_end,
        )
        if not rows:
            raise MarketHistoryError("first available minute day returned no candles")
        return int(rows[0]["timestamp"])


def write_chunk_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row[field] for field in CSV_FIELDS})
                count += 1
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return count


def inspect_chunk(path: Path, window: DownloadWindow | None = None) -> dict[str, Any]:
    row_count = 0
    first_timestamp = None
    last_timestamp = None
    previous_timestamp = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise MarketHistoryError(f"invalid CSV header in {path.name}")
        for row in reader:
            timestamp = int(row["timestamp"])
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise MarketHistoryError(f"duplicate or unsorted timestamp in {path.name}")
            if window and not window.start_timestamp <= timestamp <= window.end_timestamp:
                raise MarketHistoryError(f"timestamp outside chunk window in {path.name}")
            parse_candle_payload(
                {
                    "t": [timestamp],
                    "o": [row["open"]],
                    "h": [row["high"]],
                    "l": [row["low"]],
                    "c": [row["close"]],
                    "v": [row["volume"]],
                },
                symbol=row["symbol"],
                resolution=row["resolution"],
                start_timestamp=window.start_timestamp if window else timestamp,
                end_timestamp=window.end_timestamp if window else timestamp,
            )
            first_timestamp = timestamp if first_timestamp is None else first_timestamp
            last_timestamp = timestamp
            previous_timestamp = timestamp
            row_count += 1
    return {
        "path": str(path.resolve()),
        "rows": row_count,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
    }


def merge_chunks_atomic(chunk_paths: Iterable[Path], output_path: Path) -> dict[str, Any]:
    paths = sorted(chunk_paths, key=lambda item: item.name)
    if not paths:
        raise MarketHistoryError("no chunk files available for merge")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    row_count = 0
    first_timestamp = None
    last_timestamp = None
    gap_buckets = {"1m": 0, "2_5m": 0, "6_15m": 0, "gt_15m": 0}
    try:
        with temporary.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for path in paths:
                with path.open("r", encoding="utf-8", newline="") as source:
                    reader = csv.DictReader(source)
                    if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                        raise MarketHistoryError(f"invalid CSV header in {path.name}")
                    for row in reader:
                        timestamp = int(row["timestamp"])
                        if last_timestamp is not None:
                            if timestamp <= last_timestamp:
                                raise MarketHistoryError("chunk merge found duplicate or unsorted timestamps")
                            delta_minutes = (timestamp - last_timestamp) // 60
                            if delta_minutes == 1:
                                gap_buckets["1m"] += 1
                            elif delta_minutes <= 5:
                                gap_buckets["2_5m"] += 1
                            elif delta_minutes <= 15:
                                gap_buckets["6_15m"] += 1
                            else:
                                gap_buckets["gt_15m"] += 1
                        writer.writerow({field: row[field] for field in CSV_FIELDS})
                        first_timestamp = timestamp if first_timestamp is None else first_timestamp
                        last_timestamp = timestamp
                        row_count += 1
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "output_path": str(output_path.resolve()),
        "chunk_count": len(paths),
        "rows": row_count,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "gap_buckets": gap_buckets,
    }


def write_manifest_atomic(path: Path, *, config: DownloadConfig, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "summary": summary,
    }
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
