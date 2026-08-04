from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.data.market_history import parse_candle_payload, write_chunk_atomic
from backend.evaluation.historical_dataset import (
    batched,
    build_evaluation_manifest,
    iter_partition_candles,
    verify_manifest_contract,
    verify_manifest_sources,
)


def _write_history(root: Path, *, chunks: int = 3, rows_per_chunk: int = 100) -> Path:
    chunks_dir = root / "chunks"
    chunks_dir.mkdir()
    timestamp = 1_800_000_000
    for _ in range(chunks):
        start = timestamp
        timestamps = [start + index * 60 for index in range(rows_per_chunk)]
        prices = [100 + index / 100 for index in range(rows_per_chunk)]
        rows = parse_candle_payload(
            {
                "t": timestamps,
                "o": prices,
                "h": [price + 1 for price in prices],
                "l": [price - 1 for price in prices],
                "c": prices,
                "v": [10] * rows_per_chunk,
            },
            symbol="BTC-BRL",
            resolution="1m",
            start_timestamp=timestamps[0],
            end_timestamp=timestamps[-1],
        )
        write_chunk_atomic(chunks_dir / f"{timestamps[0]}_{timestamps[-1]}.csv", rows)
        timestamp = timestamps[-1] + 60
    return chunks_dir


def test_manifest_is_reproducible_and_partitions_are_purged(tmp_path: Path):
    chunks_dir = _write_history(tmp_path)
    generated = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = build_evaluation_manifest(chunks_dir, purge_minutes=5, generated_at=generated)
    second = build_evaluation_manifest(chunks_dir, purge_minutes=5, generated_at=generated)

    assert first["dataset_id"] == second["dataset_id"]
    assert first["source"]["rows"] == 300
    assert first["partitions"]["holdout"]["sealed"] is True
    development = first["partitions"]["development"]
    validation = first["partitions"]["validation"]
    holdout = first["partitions"]["holdout"]
    assert development["end_timestamp"] + 10 * 60 < validation["start_timestamp"]
    assert validation["end_timestamp"] + 10 * 60 < holdout["start_timestamp"]
    assert sum(partition["rows"] for partition in first["partitions"].values()) < 300


def test_manifest_detects_source_tampering(tmp_path: Path):
    chunks_dir = _write_history(tmp_path)
    manifest = build_evaluation_manifest(chunks_dir, purge_minutes=1)
    verify_manifest_sources(manifest)
    first_chunk = chunks_dir / manifest["source"]["chunks"][0]["filename"]
    first_chunk.write_text(first_chunk.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source changed"):
        verify_manifest_sources(manifest)


def test_manifest_detects_partition_contract_tampering(tmp_path: Path):
    chunks_dir = _write_history(tmp_path)
    manifest = build_evaluation_manifest(chunks_dir, purge_minutes=1)
    verify_manifest_contract(manifest)
    manifest["partitions"]["holdout"]["start_timestamp"] += 60

    with pytest.raises(ValueError, match="does not match dataset_id"):
        verify_manifest_contract(manifest)


def test_partition_iterator_and_batching_follow_manifest(tmp_path: Path):
    chunks_dir = _write_history(tmp_path)
    manifest = build_evaluation_manifest(chunks_dir, purge_minutes=1)
    rows = list(iter_partition_candles(manifest, "validation"))
    groups = list(batched(rows, 17))

    assert len(rows) == manifest["partitions"]["validation"]["rows"]
    assert sum(map(len, groups)) == len(rows)
    assert all(row["asset"] == "BTC/BRL" and row["timeframe"] == "1m" for row in rows)
    assert all(len(group) <= 17 for group in groups)


def test_batching_rejects_invalid_size():
    with pytest.raises(ValueError, match="positive"):
        list(batched([], 0))
