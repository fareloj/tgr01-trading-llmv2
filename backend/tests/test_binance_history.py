from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import requests

from backend.data.binance_history import (
    ArchiveMonth,
    BinanceArchiveConfig,
    BinanceHistoryClient,
    BinanceHistoryError,
    archive_filename,
    extract_month_to_canonical_csv,
    iter_archive_months,
    parse_checksum,
    previous_closed_month,
    normalize_open_timestamp,
    read_month_metadata,
    write_month_metadata_atomic,
    verify_archive_checksum,
)


def kline_row(timestamp: int, *, microseconds: bool = False) -> list[str]:
    raw_timestamp = timestamp * (1_000_000 if microseconds else 1_000)
    return [
        str(raw_timestamp),
        "100.0",
        "102.0",
        "99.0",
        "101.0",
        "12.5",
        str(raw_timestamp + (59_999_999 if microseconds else 59_999)),
        "1262.5",
        "42",
        "6.0",
        "606.0",
        "0",
    ]


def archive_bytes(config: BinanceArchiveConfig, month: ArchiveMonth, rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    csv_name = archive_filename(config, month).removesuffix(".zip") + ".csv"
    text = "\n".join(",".join(row) for row in rows) + "\n"
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(csv_name, text)
    return buffer.getvalue()


def test_archive_month_iteration_and_previous_closed_month():
    months = iter_archive_months(ArchiveMonth(2024, 11), ArchiveMonth(2025, 2))

    assert [item.key for item in months] == ["2024-11", "2024-12", "2025-01", "2025-02"]
    assert previous_closed_month(pd.Timestamp("2026-01-15", tz="UTC").to_pydatetime()).key == "2025-12"


def test_historical_timestamp_offset_requires_explicit_bounded_realignment():
    shifted_milliseconds = (1_512_367_220 * 1_000) + 799

    with pytest.raises(BinanceHistoryError, match="minute grid"):
        normalize_open_timestamp(shifted_milliseconds)

    assert normalize_open_timestamp(shifted_milliseconds, max_offset_seconds=30) == 1_512_367_260


@pytest.mark.parametrize(
    ("month", "microseconds"),
    [(ArchiveMonth(2024, 12), False), (ArchiveMonth(2025, 1), True)],
)
def test_extract_month_normalizes_millisecond_and_microsecond_timestamps(
    tmp_path: Path, month: ArchiveMonth, microseconds: bool
):
    config = BinanceArchiveConfig()
    start = month.bounds().start_timestamp
    raw = archive_bytes(config, month, [kline_row(start, microseconds=microseconds), kline_row(start + 60, microseconds=microseconds)])
    archive_path = tmp_path / archive_filename(config, month)
    archive_path.write_bytes(raw)

    summary = extract_month_to_canonical_csv(archive_path, tmp_path / "canonical.csv", month, config)
    frame = pd.read_csv(tmp_path / "canonical.csv")

    assert summary["rows"] == 2
    assert frame["timestamp"].tolist() == [start, start + 60]
    assert frame["source"].unique().tolist() == ["binance_public_data_spot"]
    assert frame["symbol"].unique().tolist() == ["BTCUSDT"]


def test_checksum_requires_expected_filename_and_detects_corruption(tmp_path: Path):
    archive_path = tmp_path / "BTCUSDT-1m-2024-01.zip"
    archive_path.write_bytes(b"trusted archive")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = tmp_path / "archive.CHECKSUM"
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")

    assert parse_checksum(checksum_path.read_text(), archive_path.name) == digest
    assert verify_archive_checksum(archive_path, checksum_path) == digest
    with pytest.raises(BinanceHistoryError, match="filename"):
        parse_checksum(checksum_path.read_text(), "different.zip")

    archive_path.write_bytes(b"corrupted")
    with pytest.raises(BinanceHistoryError, match="SHA-256"):
        verify_archive_checksum(archive_path, checksum_path)


def test_month_metadata_is_atomic_and_bound_to_archive_digest(tmp_path: Path):
    month = ArchiveMonth(2024, 1)
    path = tmp_path / "month.metadata.json"
    extraction = {
        "rows": 10,
        "first_timestamp": month.bounds().start_timestamp,
        "last_timestamp": month.bounds().start_timestamp + 540,
        "realigned_rows": 3,
        "maximum_realign_seconds": 20,
    }

    write_month_metadata_atomic(path, month=month, archive_sha256="a" * 64, extraction=extraction)
    loaded = read_month_metadata(path, month=month, archive_sha256="a" * 64)

    assert loaded["realigned_rows"] == 3
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    with pytest.raises(BinanceHistoryError, match="does not match"):
        read_month_metadata(path, month=month, archive_sha256="b" * 64)


def test_extract_rejects_unexpected_zip_member(tmp_path: Path):
    month = ArchiveMonth(2024, 1)
    archive_path = tmp_path / archive_filename(BinanceArchiveConfig(), month)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("unexpected.csv", "not,a,kline\n")

    with pytest.raises(BinanceHistoryError, match="expected CSV"):
        extract_month_to_canonical_csv(archive_path, tmp_path / "output.csv", month)


class FakeResponse:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_latest_published_month_steps_back_when_archive_is_not_ready(tmp_path: Path):
    config = BinanceArchiveConfig(max_attempts=1, minimum_request_interval_seconds=0)
    published = ArchiveMonth(2026, 6)
    filename = archive_filename(config, published)
    digest = "a" * 64
    session = FakeSession(
        [
            FakeResponse(404, b""),
            FakeResponse(200, f"{digest}  {filename}\n".encode()),
        ]
    )
    client = BinanceHistoryClient(config, session=session, sleep=lambda _seconds: None)

    result = client.discover_latest_published_month(ArchiveMonth(2026, 7), tmp_path)

    assert result == published
    assert session.calls == 2


def test_client_downloads_checksum_and_archive_atomically(tmp_path: Path):
    config = BinanceArchiveConfig(max_attempts=2, minimum_request_interval_seconds=0)
    month = ArchiveMonth(2024, 1)
    body = archive_bytes(config, month, [kline_row(month.bounds().start_timestamp)])
    digest = hashlib.sha256(body).hexdigest()
    session = FakeSession(
        [
            FakeResponse(200, f"{digest}  {archive_filename(config, month)}\n".encode()),
            FakeResponse(200, body),
        ]
    )
    client = BinanceHistoryClient(config, session=session, sleep=lambda _seconds: None)
    archive_path = tmp_path / archive_filename(config, month)
    checksum_path = tmp_path / f"{archive_path.name}.CHECKSUM"

    actual = client.ensure_month_archive(month, archive_path, checksum_path)

    assert actual == digest
    assert session.calls == 2
    assert not list(tmp_path.glob("*.tmp"))
