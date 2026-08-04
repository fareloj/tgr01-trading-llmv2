from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.evaluation.historical_dataset import build_evaluation_manifest, write_manifest_atomic


DEFAULT_CHUNKS = PROJECT_DIR / "backend" / "data_exports" / "mercado_bitcoin_btc_brl_1m" / "chunks"
DEFAULT_OUTPUT = PROJECT_DIR / "backend" / "data_exports" / "historical_evaluation" / "manifest.json"
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def local_label(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze chronological BTC/BRL evaluation partitions.")
    parser.add_argument("--chunks-dir", default=str(DEFAULT_CHUNKS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--development-ratio", type=float, default=0.60)
    parser.add_argument("--validation-ratio", type=float, default=0.20)
    parser.add_argument("--purge-minutes", type=int, default=1_440)
    args = parser.parse_args()

    manifest = build_evaluation_manifest(
        Path(args.chunks_dir),
        development_ratio=args.development_ratio,
        validation_ratio=args.validation_ratio,
        purge_minutes=args.purge_minutes,
    )
    output = Path(args.output)
    write_manifest_atomic(output, manifest)
    print(f"Dataset: {manifest['dataset_id']}")
    print(f"Source candles: {manifest['source']['rows']}")
    for name, partition in manifest["partitions"].items():
        print(
            f"{name}: rows={partition['rows']} coverage={partition['observed_coverage']:.2%} "
            f"range={local_label(partition['start_timestamp'])}..{local_label(partition['end_timestamp'])} "
            f"sealed={partition['sealed']}"
        )
    print(f"Manifest: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
