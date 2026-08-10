from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.dataset import FEATURE_COLUMNS
from backend.ml.slow_dataset import SLOW_TCN_FEATURE_COLUMNS, build_slow_tcn_dataset


REPORTS_DIR = PROJECT_DIR / "backend" / "reports"
DEFAULT_LOCAL = REPORTS_DIR / "mb_tcn_dataset.csv"
DEFAULT_GLOBAL = REPORTS_DIR / "binance_full_dataset.csv"
DEFAULT_OUTPUT = REPORTS_DIR / "mb_slow_tcn_v2.csv"
DEFAULT_METADATA = REPORTS_DIR / "mb_slow_tcn_v2.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build synchronized 15-minute BTC/BRL rows for the TCN v2 experiment."
    )
    parser.add_argument("--local-dataset", default=str(DEFAULT_LOCAL))
    parser.add_argument("--global-dataset", default=str(DEFAULT_GLOBAL))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--cadence-minutes", type=int, default=15)
    parser.add_argument("--horizons", type=int, nargs="+", default=[240, 1_440])
    parser.add_argument("--actionable-move-pct", type=float, default=0.25)
    parser.add_argument("--maximum-price-age-minutes", type=int, default=15)
    args = parser.parse_args()

    local_path = Path(args.local_dataset).resolve()
    global_path = Path(args.global_dataset).resolve()
    local_columns = ["timestamp", "close", "is_observed", *FEATURE_COLUMNS]
    print(f"Loading local features: {local_path}", flush=True)
    local = pd.read_csv(local_path, usecols=local_columns)
    print(f"Loading global closes: {global_path}", flush=True)
    global_market = pd.read_csv(global_path, usecols=["timestamp", "close", "is_observed"])
    dataset = build_slow_tcn_dataset(
        local,
        global_market,
        cadence_minutes=args.cadence_minutes,
        horizons_minutes=args.horizons,
        actionable_move_pct=args.actionable_move_pct,
        maximum_price_age_minutes=args.maximum_price_age_minutes,
    )

    output = Path(args.output).resolve()
    metadata_path = Path(args.metadata).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output, index=False)
    metadata = {
        "dataset_version": "slow_tcn_v2",
        "rows": len(dataset),
        "first_timestamp": int(dataset["timestamp"].min()),
        "last_timestamp": int(dataset["timestamp"].max()),
        "cadence_minutes": args.cadence_minutes,
        "horizons_minutes": args.horizons,
        "actionable_move_pct": args.actionable_move_pct,
        "maximum_price_age_minutes": args.maximum_price_age_minutes,
        "feature_columns": list(SLOW_TCN_FEATURE_COLUMNS),
        "local_source": str(local_path),
        "global_source": str(global_path),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
