from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.chunked import build_dataset_from_chunks
from backend.ml.dataset import DatasetConfig


DEFAULT_CHUNKS = PROJECT_DIR / "backend" / "data_exports" / "mercado_bitcoin_btc_brl_1m" / "chunks"
DEFAULT_OUTPUT = PROJECT_DIR / "backend" / "reports" / "mb_tcn_dataset.csv"
DEFAULT_METADATA = PROJECT_DIR / "backend" / "reports" / "mb_tcn_dataset.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a minute-regular BTC/BRL context dataset for causal sequence models."
    )
    parser.add_argument("--chunks-dir", default=str(DEFAULT_CHUNKS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()
    if args.progress_every <= 0:
        parser.error("--progress-every must be positive")

    def progress(index: int, total: int, path: Path, rows: int) -> None:
        if index == 1 or index == total or index % args.progress_every == 0:
            print(f"[{index}/{total}] {path.name}: {rows} context rows", flush=True)

    summary = build_dataset_from_chunks(
        Path(args.chunks_dir),
        Path(args.output),
        config=DatasetConfig(),
        metadata_path=Path(args.metadata),
        progress=progress,
        include_context_rows=True,
    )
    print(f"rows={summary['rows']} labels={summary['label_distribution']}")
    print(f"output={Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
