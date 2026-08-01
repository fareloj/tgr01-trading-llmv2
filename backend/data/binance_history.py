from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from backend.data.market_history import CSV_FIELDS, DownloadWindow, MarketHistoryError, inspect_chunk


BINANCE_ARCHIVE_ROOT = "https://data.binance.vision/data/spot/monthly/klines"
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
CHECKSUM_PATTERN = re.compile(r"^([0-9a-fA-F]{64})\s+\*?([^\s]+)\s*$")


class BinanceHistoryError(MarketHistoryError):
    pass


class BinanceArchiveNotFound(BinanceHistoryError):
    pass


@dataclass(frozen=True, order=True)
class ArchiveMonth:
    year: int
    month: int

    def __post_init__(self) -> None:
        if self.year < 2010 or not 1 <= self.month <= 12:
            raise ValueError("invalid archive month")

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def next(self) -> ArchiveMonth:
        if self.month == 12:
            return ArchiveMonth(self.year + 1, 1)
        return ArchiveMonth(self.year, self.month + 1)

    def previous(self) -> ArchiveMonth:
        if self.month == 1:
            return ArchiveMonth(self.year - 1, 12)
        return ArchiveMonth(self.year, self.month - 1)

    def bounds(self) -> DownloadWindow:
        start = int(datetime(self.year, self.month, 1, tzinfo=timezone.utc).timestamp())
        next_month = self.next()
        end = int(datetime(next_month.year, next_month.month, 1, tzinfo=timezone.utc).timestamp()) - 60
        return DownloadWindow(start, end)


@dataclass(frozen=True)
class BinanceArchiveConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    timeout_seconds: float = 45.0
    max_attempts: int = 5
    minimum_request_interval_seconds: float = 0.10
    max_archive_bytes: int = 64 * 1024 * 1024
    max_uncompressed_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: float = 250.0
    max_historical_timestamp_offset_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.symbol.isalnum() or self.symbol.upper() != self.symbol:
            raise ValueError("symbol must be an uppercase alphanumeric Binance symbol")
        if self.interval != "1m":
            raise ValueError("global pretraining downloader currently supports only 1m klines")
        if self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ValueError("timeout and max_attempts must be positive")
        if self.minimum_request_interval_seconds < 0:
            raise ValueError("minimum request interval must be non-negative")
        if self.max_archive_bytes <= 0 or self.max_uncompressed_bytes <= 0:
            raise ValueError("archive size limits must be positive")
        if self.max_compression_ratio <= 1:
            raise ValueError("compression ratio limit must be greater than one")
        if not 0 <= self.max_historical_timestamp_offset_seconds <= 30:
            raise ValueError("historical timestamp offset tolerance must be between zero and 30 seconds")


def iter_archive_months(start: ArchiveMonth, end: ArchiveMonth) -> list[ArchiveMonth]:
    if end < start:
        raise ValueError("end month must not be earlier than start month")
    result = []
    cursor = start
    while cursor <= end:
        result.append(cursor)
        cursor = cursor.next()
    return result


def previous_closed_month(now: datetime | None = None) -> ArchiveMonth:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current.month == 1:
        return ArchiveMonth(current.year - 1, 12)
    return ArchiveMonth(current.year, current.month - 1)


def archive_filename(config: BinanceArchiveConfig, month: ArchiveMonth) -> str:
    return f"{config.symbol}-{config.interval}-{month.key}.zip"


def archive_url(config: BinanceArchiveConfig, month: ArchiveMonth) -> str:
    filename = archive_filename(config, month)
    return f"{BINANCE_ARCHIVE_ROOT}/{config.symbol}/{config.interval}/{filename}"


def parse_checksum(text: str, expected_filename: str) -> str:
    match = CHECKSUM_PATTERN.match(text.strip())
    if not match:
        raise BinanceHistoryError("invalid Binance checksum document")
    digest, filename = match.groups()
    if Path(filename).name != expected_filename:
        raise BinanceHistoryError("checksum filename does not match requested archive")
    return digest.lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive_checksum(archive_path: Path, checksum_path: Path) -> str:
    expected = parse_checksum(checksum_path.read_text(encoding="utf-8"), archive_path.name)
    actual = sha256_file(archive_path)
    if actual != expected:
        raise BinanceHistoryError(f"SHA-256 mismatch for {archive_path.name}")
    return actual


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceHistoryError(f"invalid Binance {field}") from exc
    if not math.isfinite(result):
        raise BinanceHistoryError(f"non-finite Binance {field}")
    return result


def _raw_timestamp_seconds(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BinanceHistoryError("invalid Binance open timestamp") from exc
    return value // 1_000_000 if value >= 10**15 else value // 1_000


def normalize_open_timestamp(raw: Any, *, max_offset_seconds: int = 0) -> int:
    timestamp = _raw_timestamp_seconds(raw)
    remainder = timestamp % 60
    if remainder:
        nearest_offset = min(remainder, 60 - remainder)
        if nearest_offset > max_offset_seconds:
            raise BinanceHistoryError("Binance kline timestamp is outside the one-minute grid")
        # Historical Binance archives contain a documented-by-data interval
        # shifted by 20.799 seconds. Ceiling keeps consecutive 60-second bars
        # unique; flooring would collide with the preceding short candle.
        timestamp += 60 - remainder
    return timestamp


def _validated_zip_member(
    archive: zipfile.ZipFile,
    *,
    expected_csv_name: str,
    config: BinanceArchiveConfig,
) -> zipfile.ZipInfo:
    files = [item for item in archive.infolist() if not item.is_dir()]
    if len(files) != 1 or Path(files[0].filename).name != expected_csv_name:
        raise BinanceHistoryError("Binance archive must contain exactly the expected CSV")
    member = files[0]
    if member.file_size > config.max_uncompressed_bytes:
        raise BinanceHistoryError("Binance archive exceeds the uncompressed size limit")
    if member.compress_size <= 0 and member.file_size > 0:
        raise BinanceHistoryError("Binance archive has an invalid compressed size")
    ratio = member.file_size / max(member.compress_size, 1)
    if ratio > config.max_compression_ratio:
        raise BinanceHistoryError("Binance archive exceeds the compression-ratio limit")
    return member


def extract_month_to_canonical_csv(
    archive_path: Path,
    output_path: Path,
    month: ArchiveMonth,
    config: BinanceArchiveConfig | None = None,
) -> dict[str, Any]:
    config = config or BinanceArchiveConfig()
    if archive_path.stat().st_size > config.max_archive_bytes:
        raise BinanceHistoryError("Binance archive exceeds the compressed size limit")
    expected_csv_name = archive_filename(config, month).removesuffix(".zip") + ".csv"
    bounds = month.bounds()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    rows = 0
    realigned_rows = 0
    maximum_realign_seconds = 0
    first_timestamp = None
    last_timestamp = None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            member = _validated_zip_member(archive, expected_csv_name=expected_csv_name, config=config)
            with archive.open(member) as source, temporary.open("w", encoding="utf-8", newline="") as target:
                decoded = (line.decode("utf-8") for line in source)
                reader = csv.reader(decoded)
                writer = csv.DictWriter(target, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for raw in reader:
                    if len(raw) != 12:
                        raise BinanceHistoryError("Binance kline row must contain 12 columns")
                    source_timestamp = _raw_timestamp_seconds(raw[0])
                    timestamp = normalize_open_timestamp(
                        raw[0],
                        max_offset_seconds=config.max_historical_timestamp_offset_seconds,
                    )
                    if timestamp != source_timestamp:
                        realigned_rows += 1
                        maximum_realign_seconds = max(
                            maximum_realign_seconds,
                            abs(timestamp - source_timestamp),
                        )
                    if not bounds.start_timestamp <= timestamp <= bounds.end_timestamp:
                        raise BinanceHistoryError("Binance archive contains a candle outside its month")
                    if last_timestamp is not None and timestamp <= last_timestamp:
                        raise BinanceHistoryError("Binance archive contains duplicate or unsorted candles")
                    open_price = _finite_number(raw[1], "open")
                    high = _finite_number(raw[2], "high")
                    low = _finite_number(raw[3], "low")
                    close = _finite_number(raw[4], "close")
                    volume = _finite_number(raw[5], "volume")
                    if min(open_price, high, low, close) <= 0 or volume < 0:
                        raise BinanceHistoryError("Binance archive contains invalid prices or volume")
                    if high < max(open_price, close, low) or low > min(open_price, close, high):
                        raise BinanceHistoryError("Binance archive contains inconsistent OHLC ranges")
                    writer.writerow(
                        {
                            "timestamp": timestamp,
                            "datetime_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                            "open": open_price,
                            "high": high,
                            "low": low,
                            "close": close,
                            "volume": volume,
                            "symbol": config.symbol,
                            "resolution": config.interval,
                            "source": "binance_public_data_spot",
                        }
                    )
                    first_timestamp = timestamp if first_timestamp is None else first_timestamp
                    last_timestamp = timestamp
                    rows += 1
        if rows == 0:
            raise BinanceHistoryError("Binance archive contains no candles")
        os.replace(temporary, output_path)
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise BinanceHistoryError(f"invalid Binance ZIP archive: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    inspection = inspect_chunk(output_path, bounds)
    if inspection["rows"] != rows:
        raise BinanceHistoryError("canonical Binance row count changed after extraction")
    return {
        "path": str(output_path.resolve()),
        "rows": rows,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "realigned_rows": realigned_rows,
        "maximum_realign_seconds": maximum_realign_seconds,
    }


class BinanceHistoryClient:
    def __init__(
        self,
        config: BinanceArchiveConfig | None = None,
        *,
        session: requests.Session | None = None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        self.config = config or BinanceArchiveConfig()
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

    def _download_atomic(self, url: str, path: Path, *, max_bytes: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        last_error: Exception | None = None
        try:
            for attempt in range(self.config.max_attempts):
                self._throttle()
                try:
                    response = self.session.get(url, timeout=self.config.timeout_seconds, stream=True)
                    if response.status_code == 404:
                        raise BinanceArchiveNotFound(f"archive not found: {url}")
                    if response.status_code in TRANSIENT_STATUS_CODES:
                        raise BinanceHistoryError(f"transient HTTP {response.status_code}")
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        raise BinanceHistoryError("remote Binance file exceeds the size limit")
                    written = 0
                    with temporary.open("wb") as handle:
                        for block in response.iter_content(chunk_size=1024 * 1024):
                            if not block:
                                continue
                            written += len(block)
                            if written > max_bytes:
                                raise BinanceHistoryError("downloaded Binance file exceeds the size limit")
                            handle.write(block)
                    if written == 0:
                        raise BinanceHistoryError("downloaded Binance file is empty")
                    os.replace(temporary, path)
                    return
                except BinanceArchiveNotFound:
                    raise
                except (requests.RequestException, ValueError, BinanceHistoryError) as exc:
                    last_error = exc
                    if temporary.exists():
                        temporary.unlink()
                    if attempt + 1 < self.config.max_attempts:
                        self._sleep(min(2**attempt, 30))
            raise BinanceHistoryError(f"Binance download failed after retries: {last_error}") from last_error
        finally:
            if temporary.exists():
                temporary.unlink()

    def ensure_month_archive(
        self,
        month: ArchiveMonth,
        archive_path: Path,
        checksum_path: Path,
        *,
        force: bool = False,
    ) -> str:
        url = archive_url(self.config, month)
        if force or not checksum_path.exists():
            self._download_atomic(url + ".CHECKSUM", checksum_path, max_bytes=16 * 1024)
        if force or not archive_path.exists():
            self._download_atomic(url, archive_path, max_bytes=self.config.max_archive_bytes)
        try:
            return verify_archive_checksum(archive_path, checksum_path)
        except BinanceHistoryError:
            if force:
                raise
            archive_path.unlink(missing_ok=True)
            checksum_path.unlink(missing_ok=True)
            self._download_atomic(url + ".CHECKSUM", checksum_path, max_bytes=16 * 1024)
            self._download_atomic(url, archive_path, max_bytes=self.config.max_archive_bytes)
            return verify_archive_checksum(archive_path, checksum_path)

    def discover_latest_published_month(
        self,
        candidate: ArchiveMonth,
        checksums_dir: Path,
        *,
        max_lookback_months: int = 3,
    ) -> ArchiveMonth:
        if max_lookback_months <= 0:
            raise ValueError("max lookback must be positive")
        month = candidate
        for _ in range(max_lookback_months):
            filename = archive_filename(self.config, month)
            checksum_path = checksums_dir / f"{filename}.CHECKSUM"
            try:
                if not checksum_path.exists():
                    self._download_atomic(
                        archive_url(self.config, month) + ".CHECKSUM",
                        checksum_path,
                        max_bytes=16 * 1024,
                    )
                parse_checksum(checksum_path.read_text(encoding="utf-8"), filename)
                return month
            except BinanceArchiveNotFound:
                checksum_path.unlink(missing_ok=True)
                month = month.previous()
        raise BinanceArchiveNotFound("no published monthly archive found within the lookback window")


def canonical_chunk_path(chunks_dir: Path, month: ArchiveMonth) -> Path:
    bounds = month.bounds()
    return chunks_dir / bounds.filename


def inspect_month_chunk(path: Path, month: ArchiveMonth) -> dict[str, Any]:
    return inspect_chunk(path, month.bounds())


def write_month_metadata_atomic(
    path: Path,
    *,
    month: ArchiveMonth,
    archive_sha256: str,
    extraction: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "month": month.key,
        "archive_sha256": archive_sha256,
        "rows": int(extraction["rows"]),
        "first_timestamp": int(extraction["first_timestamp"]),
        "last_timestamp": int(extraction["last_timestamp"]),
        "realigned_rows": int(extraction.get("realigned_rows", 0)),
        "maximum_realign_seconds": int(extraction.get("maximum_realign_seconds", 0)),
    }
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_month_metadata(
    path: Path,
    *,
    month: ArchiveMonth,
    archive_sha256: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BinanceHistoryError("invalid Binance month metadata") from exc
    if not isinstance(payload, dict):
        raise BinanceHistoryError("Binance month metadata must be an object")
    if payload.get("month") != month.key or payload.get("archive_sha256") != archive_sha256:
        raise BinanceHistoryError("Binance month metadata does not match the verified archive")
    numeric_fields = (
        "rows",
        "first_timestamp",
        "last_timestamp",
        "realigned_rows",
        "maximum_realign_seconds",
    )
    try:
        normalized = {field: int(payload[field]) for field in numeric_fields}
    except (KeyError, TypeError, ValueError) as exc:
        raise BinanceHistoryError("Binance month metadata has invalid numeric fields") from exc
    if (
        normalized["rows"] <= 0
        or not 0 <= normalized["realigned_rows"] <= normalized["rows"]
        or not 0 <= normalized["maximum_realign_seconds"] < 60
        or normalized["last_timestamp"] < normalized["first_timestamp"]
    ):
        raise BinanceHistoryError("Binance month metadata has invalid counts")
    return {"month": month.key, "archive_sha256": archive_sha256, **normalized}
