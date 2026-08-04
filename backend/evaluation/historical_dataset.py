from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from backend.data.market_history import CSV_FIELDS, align_minute
from backend.ml.chunked import chunk_window_from_path, list_chunk_files


SCHEMA_VERSION = 1
SPLIT_NAMES = ("development", "validation", "holdout")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def inspect_source_chunks(chunks_dir: Path) -> list[dict]:
    files = list_chunk_files(chunks_dir)
    if not files:
        raise ValueError(f"no historical chunks found in {chunks_dir}")

    inspections = []
    previous_window_end = None
    previous_observed = None
    for path in files:
        window_start, window_end = chunk_window_from_path(path)
        if previous_window_end is not None and window_start <= previous_window_end:
            raise ValueError(f"overlapping chunk windows around {path.name}")
        rows = 0
        first_timestamp = None
        last_timestamp = None
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise ValueError(f"invalid historical CSV header in {path.name}")
            for row in reader:
                timestamp = int(row["timestamp"])
                if timestamp % 60 or not window_start <= timestamp <= window_end:
                    raise ValueError(f"timestamp outside one-minute chunk window in {path.name}")
                if last_timestamp is not None and timestamp <= last_timestamp:
                    raise ValueError(f"duplicate or unsorted timestamp in {path.name}")
                if previous_observed is not None and timestamp <= previous_observed:
                    raise ValueError(f"duplicate or unsorted timestamp across chunks at {path.name}")
                prices = [float(row[key]) for key in ("open", "high", "low", "close")]
                volume = float(row["volume"])
                if not all(math.isfinite(value) and value > 0 for value in prices):
                    raise ValueError(f"invalid price in {path.name}")
                if not math.isfinite(volume) or volume < 0:
                    raise ValueError(f"invalid volume in {path.name}")
                if prices[1] < max(prices[0], prices[2], prices[3]) or prices[2] > min(
                    prices[0], prices[1], prices[3]
                ):
                    raise ValueError(f"inconsistent OHLC in {path.name}")
                first_timestamp = timestamp if first_timestamp is None else first_timestamp
                last_timestamp = timestamp
                previous_observed = timestamp
                rows += 1
        inspections.append(
            {
                "filename": path.name,
                "window_start_timestamp": window_start,
                "window_end_timestamp": window_end,
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
                "rows": rows,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
        previous_window_end = window_end
    if not any(item["rows"] for item in inspections):
        raise ValueError("historical chunks contain no candles")
    return inspections


def _partition_ranges(
    first_timestamp: int,
    last_timestamp: int,
    *,
    development_ratio: float,
    validation_ratio: float,
    purge_minutes: int,
) -> dict[str, dict]:
    if not 0 < development_ratio < 1 or not 0 < validation_ratio < 1:
        raise ValueError("split ratios must be between zero and one")
    if development_ratio + validation_ratio >= 1:
        raise ValueError("development and validation ratios must leave a holdout")
    if purge_minutes < 0:
        raise ValueError("purge_minutes must be non-negative")

    duration = last_timestamp - first_timestamp
    first_cut = align_minute(first_timestamp + int(duration * development_ratio))
    second_cut = align_minute(first_timestamp + int(duration * (development_ratio + validation_ratio)))
    purge = purge_minutes * 60
    ranges = {
        "development": {"start_timestamp": first_timestamp, "end_timestamp": first_cut - purge - 60},
        "validation": {"start_timestamp": first_cut + purge, "end_timestamp": second_cut - purge - 60},
        "holdout": {"start_timestamp": second_cut + purge, "end_timestamp": last_timestamp},
    }
    if any(item["end_timestamp"] < item["start_timestamp"] for item in ranges.values()):
        raise ValueError("history is too short for the requested split and purge intervals")
    return ranges


def _summarize_partitions(chunks_dir: Path, sources: list[dict], ranges: dict[str, dict]) -> None:
    state = {
        name: {"rows": 0, "first_timestamp": None, "last_timestamp": None, "maximum_gap_minutes": 0}
        for name in SPLIT_NAMES
    }
    for source in sources:
        path = chunks_dir / source["filename"]
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamp = int(row["timestamp"])
                for name, bounds in ranges.items():
                    if bounds["start_timestamp"] <= timestamp <= bounds["end_timestamp"]:
                        current = state[name]
                        previous = current["last_timestamp"]
                        if previous is not None:
                            current["maximum_gap_minutes"] = max(
                                current["maximum_gap_minutes"], (timestamp - previous) // 60 - 1
                            )
                        current["first_timestamp"] = (
                            timestamp if current["first_timestamp"] is None else current["first_timestamp"]
                        )
                        current["last_timestamp"] = timestamp
                        current["rows"] += 1
                        break

    for name, bounds in ranges.items():
        summary = state[name]
        if summary["rows"] == 0:
            raise ValueError(f"partition {name} contains no observed candles")
        expected = (bounds["end_timestamp"] - bounds["start_timestamp"]) // 60 + 1
        bounds.update(
            {
                **summary,
                "expected_minutes": expected,
                "observed_coverage": round(summary["rows"] / expected, 6),
                "sealed": name == "holdout",
                "purpose": {
                    "development": "prompt and deterministic-rule research",
                    "validation": "candidate selection without holdout access",
                    "holdout": "final evaluation only; do not tune on this partition",
                }[name],
            }
        )


def build_evaluation_manifest(
    chunks_dir: Path,
    *,
    development_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    purge_minutes: int = 1_440,
    generated_at: datetime | None = None,
) -> dict:
    chunks_dir = chunks_dir.resolve()
    sources = inspect_source_chunks(chunks_dir)
    observed = [item for item in sources if item["rows"]]
    first_timestamp = int(observed[0]["first_timestamp"])
    last_timestamp = int(observed[-1]["last_timestamp"])
    ranges = _partition_ranges(
        first_timestamp,
        last_timestamp,
        development_ratio=development_ratio,
        validation_ratio=validation_ratio,
        purge_minutes=purge_minutes,
    )
    _summarize_partitions(chunks_dir, sources, ranges)
    source_contract = [
        {key: item[key] for key in ("filename", "rows", "first_timestamp", "last_timestamp", "sha256")}
        for item in sources
    ]
    source_fingerprint = _stable_hash(source_contract)
    contract = {
        "schema_version": SCHEMA_VERSION,
        "source_fingerprint": source_fingerprint,
        "split_config": {
            "development_ratio": development_ratio,
            "validation_ratio": validation_ratio,
            "holdout_ratio": round(1 - development_ratio - validation_ratio, 10),
            "purge_minutes_each_side": purge_minutes,
            "partition_method": "chronological_elapsed_time",
        },
        "partitions": ranges,
    }
    return {
        **contract,
        "dataset_id": _stable_hash(contract)[:24],
        "generated_at_utc": (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "source": {
            "provider": "Mercado Bitcoin public API v4",
            "symbol": "BTC-BRL",
            "resolution": "1m",
            "chunks_dir": str(chunks_dir),
            "chunk_count": len(sources),
            "rows": sum(item["rows"] for item in sources),
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "chunks": sources,
        },
        "policy": {
            "news": "Candles do not reconstruct point-in-time news. Historical-news evaluation needs a separate archived news corpus.",
            "holdout": "Do not inspect holdout outcomes while changing prompts, tools, thresholds, or risk rules.",
            "real_money": "This dataset is research evidence and does not authorize live trading.",
        },
    }


def write_manifest_atomic(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_manifest_sources(manifest: dict) -> None:
    verify_manifest_contract(manifest)
    chunks_dir = Path(manifest["source"]["chunks_dir"])
    contract = []
    for item in manifest["source"]["chunks"]:
        path = chunks_dir / item["filename"]
        if not path.is_file() or _sha256(path) != item["sha256"]:
            raise ValueError(f"historical source changed or is missing: {item['filename']}")
        contract.append(
            {key: item[key] for key in ("filename", "rows", "first_timestamp", "last_timestamp", "sha256")}
        )
    if _stable_hash(contract) != manifest["source_fingerprint"]:
        raise ValueError("historical manifest source fingerprint is inconsistent")


def manifest_contract_id(manifest: dict) -> str:
    contract = {
        "schema_version": manifest.get("schema_version"),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "split_config": manifest.get("split_config"),
        "partitions": manifest.get("partitions"),
    }
    return _stable_hash(contract)[:24]


def verify_manifest_contract(manifest: dict) -> None:
    """Reject accidental edits to split boundaries, summaries, or source identity."""
    expected = manifest_contract_id(manifest)
    if manifest.get("dataset_id") != expected:
        raise ValueError("historical manifest contract does not match dataset_id")


def iter_partition_candles(manifest: dict, partition: str) -> Iterator[dict]:
    if partition not in SPLIT_NAMES:
        raise ValueError(f"unsupported partition: {partition}")
    bounds = manifest["partitions"][partition]
    chunks_dir = Path(manifest["source"]["chunks_dir"])
    for source in manifest["source"]["chunks"]:
        if source["last_timestamp"] is None or source["first_timestamp"] is None:
            continue
        if source["last_timestamp"] < bounds["start_timestamp"] or source["first_timestamp"] > bounds["end_timestamp"]:
            continue
        with (chunks_dir / source["filename"]).open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamp = int(row["timestamp"])
                if bounds["start_timestamp"] <= timestamp <= bounds["end_timestamp"]:
                    yield {
                        "asset": "BTC/BRL",
                        "timeframe": "1m",
                        "timestamp": timestamp,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }


def batched(rows: Iterable[dict], size: int) -> Iterator[list[dict]]:
    if size <= 0:
        raise ValueError("batch size must be positive")
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
