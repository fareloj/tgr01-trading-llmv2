from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.barriers import first_touch_barrier_targets
from backend.ml.sequences import sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Build causal first-touch direction targets.")
    parser.add_argument("dataset")
    parser.add_argument("--output", required=True)
    parser.add_argument("--horizons", nargs="+", type=int, default=(15, 60))
    parser.add_argument("--barrier-pct", type=float, default=0.20)
    args = parser.parse_args()

    source = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    frame = pd.read_csv(
        source,
        usecols=["timestamp", "high", "low", "close", "is_observed"],
        dtype={
            "timestamp": "int64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "is_observed": "boolean",
        },
    ).sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    targets = first_touch_barrier_targets(
        frame["timestamp"].to_numpy(dtype=np.int64),
        frame["high"].to_numpy(dtype=np.float64),
        frame["low"].to_numpy(dtype=np.float64),
        frame["close"].to_numpy(dtype=np.float64),
        frame["is_observed"].to_numpy(dtype=bool),
        args.horizons,
        barrier_pct=args.barrier_pct,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            timestamps=targets.timestamps,
            labels=targets.labels,
            horizons_minutes=np.asarray(targets.horizons_minutes, dtype=np.int16),
            barrier_pct=np.asarray([targets.barrier_pct], dtype=np.float32),
            source_sha256=np.asarray([sha256_file(source)]),
        )
    os.replace(temporary, output)
    valid = targets.labels >= 0
    print(f"rows={len(targets.timestamps)} valid={int(valid.sum())}/{targets.labels.size}")
    for index, horizon in enumerate(targets.horizons_minutes):
        values, counts = np.unique(targets.labels[:, index], return_counts=True)
        print(f"{horizon}m={dict(zip(values.tolist(), counts.tolist()))}")
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
