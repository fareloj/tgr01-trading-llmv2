from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from backend.ml.dataset import DatasetConfig, build_market_dataset, select_columns_for_export


ProgressCallback = Callable[[int, int, Path, int], None]
CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def chunk_window_from_path(path: Path) -> tuple[int, int]:
    try:
        start, end = path.stem.split("_", maxsplit=1)
        start_timestamp, end_timestamp = int(start), int(end)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid chunk filename: {path.name}") from exc
    if end_timestamp < start_timestamp:
        raise ValueError(f"invalid chunk timestamp range: {path.name}")
    return start_timestamp, end_timestamp


def list_chunk_files(chunks_dir: Path) -> list[Path]:
    files = []
    for path in chunks_dir.glob("*.csv"):
        start, _ = chunk_window_from_path(path)
        files.append((start, path))
    return [path for _, path in sorted(files, key=lambda item: item[0])]


def _context_files(files: list[Path], current_index: int, config: DatasetConfig) -> list[Path]:
    current_start, current_end = chunk_window_from_path(files[current_index])
    context_start = current_start - (config.minimum_history_candles + config.max_fill_gap_minutes) * 60
    context_end = current_end + max(config.horizons_minutes) * 60
    selected = []
    for path in files:
        start, end = chunk_window_from_path(path)
        if end >= context_start and start <= context_end:
            selected.append(path)
    return selected


def build_dataset_from_chunks(
    chunks_dir: Path,
    output_path: Path,
    *,
    config: DatasetConfig | None = None,
    metadata_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    config = config or DatasetConfig()
    files = list_chunk_files(chunks_dir)
    if not files:
        raise ValueError(f"no chunk CSV files found in {chunks_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    label_counts: Counter[str] = Counter()
    total_rows = 0
    first_timestamp = None
    last_timestamp = None
    try:
        header_written = False
        for index, current in enumerate(files):
            context_paths = _context_files(files, index, config)
            frames = [pd.read_csv(path, usecols=CANDLE_COLUMNS) for path in context_paths]
            source = pd.concat(frames, ignore_index=True)
            dataset = build_market_dataset(source, config)
            current_start, current_end = chunk_window_from_path(current)
            dataset = dataset.loc[dataset["timestamp"].between(current_start, current_end)].copy()
            exported = select_columns_for_export(dataset, config.horizons_minutes)
            if not exported.empty:
                exported.to_csv(
                    temporary,
                    mode="a" if header_written else "w",
                    header=not header_written,
                    index=False,
                )
                header_written = True
                total_rows += len(exported)
                label_counts.update(str(label) for label in exported["label"])
                first_timestamp = (
                    int(exported["timestamp"].min()) if first_timestamp is None else first_timestamp
                )
                last_timestamp = int(exported["timestamp"].max())
            if progress:
                progress(index + 1, len(files), current, len(exported))
        if not header_written:
            raise ValueError("chunk history produced no eligible dataset rows")
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "chunks_dir": str(chunks_dir.resolve()),
        "output_path": str(output_path.resolve()),
        "chunks_processed": len(files),
        "rows": total_rows,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "label_distribution": dict(label_counts),
        "config": config.as_dict(),
    }
    if metadata_path:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
        try:
            temporary_metadata.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary_metadata, metadata_path)
        finally:
            if temporary_metadata.exists():
                temporary_metadata.unlink()
    return summary
