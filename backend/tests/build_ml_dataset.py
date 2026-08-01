from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import klines
from backend.ml.dataset import DatasetConfig, build_market_dataset, dataset_metadata, select_columns_for_export


LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
REPORTS_DIR = PROJECT_DIR / "backend" / "reports"


def parse_local_datetime(value: str | None) -> int | None:
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return int(parsed.replace(tzinfo=LOCAL_TZ).timestamp())


def fetch_candles(asset: str, timeframe: str, from_timestamp: int | None, to_timestamp: int | None) -> pd.DataFrame:
    statement = select(
        klines.c.timestamp,
        klines.c.open,
        klines.c.high,
        klines.c.low,
        klines.c.close,
        klines.c.volume,
    ).where(klines.c.asset == asset, klines.c.timeframe == timeframe)
    if from_timestamp is not None:
        statement = statement.where(klines.c.timestamp >= from_timestamp)
    if to_timestamp is not None:
        statement = statement.where(klines.c.timestamp <= to_timestamp)
    statement = statement.order_by(klines.c.timestamp.asc())
    with database.engine.connect() as connection:
        rows = [dict(row._mapping) for row in connection.execute(statement)]
    return pd.DataFrame(rows)


def parse_horizons(value: str) -> tuple[int, ...]:
    result = tuple(sorted({int(part.strip()) for part in value.split(",") if part.strip()}))
    if not result:
        raise ValueError("at least one horizon is required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a causal BTC dataset with exact future labels.")
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--from-local")
    parser.add_argument("--to-local")
    parser.add_argument("--horizons", default="15,60")
    parser.add_argument("--primary-horizon", type=int, default=15)
    parser.add_argument("--round-trip-cost-pct", type=float, default=0.15)
    parser.add_argument("--minimum-net-edge-pct", type=float, default=0.05)
    parser.add_argument("--minimum-history", type=int, default=240)
    parser.add_argument("--max-fill-gap-minutes", type=int, default=15)
    parser.add_argument("--minimum-observed-coverage", type=float, default=0.80)
    parser.add_argument("--allow-synthetic-future", action="store_true")
    parser.add_argument("--csv-out", default=str(REPORTS_DIR / "last_ml_dataset.csv"))
    parser.add_argument("--metadata-out", default=str(REPORTS_DIR / "last_ml_dataset_metadata.json"))
    args = parser.parse_args()

    horizons = parse_horizons(args.horizons)
    config = DatasetConfig(
        horizons_minutes=horizons,
        primary_horizon_minutes=args.primary_horizon,
        round_trip_cost_pct=args.round_trip_cost_pct,
        minimum_net_edge_pct=args.minimum_net_edge_pct,
        minimum_history_candles=args.minimum_history,
        max_fill_gap_minutes=args.max_fill_gap_minutes,
        minimum_observed_coverage=args.minimum_observed_coverage,
        require_observed_future=not args.allow_synthetic_future,
    )
    candles = fetch_candles(
        args.asset,
        args.timeframe,
        parse_local_datetime(args.from_local),
        parse_local_datetime(args.to_local),
    )
    if candles.empty:
        raise SystemExit("No candles found for the requested interval.")
    dataset = build_market_dataset(candles, config)
    if dataset.empty:
        raise SystemExit("No eligible rows: check continuity, history length, and future horizons.")

    exported = select_columns_for_export(dataset, horizons)
    csv_output = Path(args.csv_out)
    metadata_output = Path(args.metadata_out)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    exported.to_csv(csv_output, index=False)

    metadata = dataset_metadata(dataset, config)
    metadata.update(
        {
            "asset": args.asset,
            "timeframe": args.timeframe,
            "source_candles": len(candles),
            "csv_path": str(csv_output.resolve()),
        }
    )
    metadata_output.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Database: {database.get_database_label()}")
    print(f"Source candles: {len(candles)}")
    print(f"Eligible dataset rows: {len(exported)}")
    print(f"Labels: {metadata['label_distribution']}")
    print(f"CSV: {csv_output.resolve()}")
    print(f"Metadata: {metadata_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
