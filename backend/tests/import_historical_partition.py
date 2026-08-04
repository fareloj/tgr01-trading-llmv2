from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database, repository
from backend.evaluation.historical_dataset import SPLIT_NAMES, batched, iter_partition_candles, verify_manifest_sources


DEFAULT_MANIFEST = PROJECT_DIR / "backend" / "data_exports" / "historical_evaluation" / "manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import one frozen historical partition into PostgreSQL.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--partition", choices=SPLIT_NAMES, required=True)
    parser.add_argument("--batch-size", type=int, default=2_000)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--skip-source-verification", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.batch_size <= 0 or (args.max_rows is not None and args.max_rows <= 0):
        parser.error("batch size and max rows must be positive")

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if not args.skip_source_verification:
        print("[VERIFY] Hashing frozen source chunks...")
        verify_manifest_sources(manifest)
    expected = int(manifest["partitions"][args.partition]["rows"])
    rows = iter_partition_candles(manifest, args.partition)
    imported = 0
    if not args.dry_run:
        database.init_db()
    for batch in batched(rows, args.batch_size):
        if args.max_rows is not None:
            remaining = args.max_rows - imported
            batch = batch[:remaining]
        if not args.dry_run:
            repository.add_klines(batch)
        imported += len(batch)
        if imported % (args.batch_size * 10) == 0 or imported == expected:
            print(f"[{args.partition}] {imported}/{expected}", flush=True)
        if args.max_rows is not None and imported >= args.max_rows:
            break
    print(f"Dataset: {manifest['dataset_id']} | partition: {args.partition}")
    print(f"Rows {'validated' if args.dry_run else 'upserted'}: {imported}")
    if args.max_rows is None and imported != expected:
        raise RuntimeError(f"manifest expected {expected} rows but iterator produced {imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
