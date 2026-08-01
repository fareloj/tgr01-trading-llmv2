from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.dataset import DatasetConfig
from backend.ml.multidomain import MarketDomain, compile_market_domains


DEFAULT_BINANCE = PROJECT_DIR / "backend" / "data_exports" / "binance_btcusdt_1m" / "chunks"
DEFAULT_MB = PROJECT_DIR / "backend" / "data_exports" / "mercado_bitcoin_btc_brl_1m" / "chunks"
DEFAULT_OUTPUT = PROJECT_DIR / "backend" / "reports" / "multidomain_ml"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile separate global-pretraining and local-calibration BTC datasets."
    )
    parser.add_argument("--binance-chunks", default=str(DEFAULT_BINANCE))
    parser.add_argument("--mb-chunks", default=str(DEFAULT_MB))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--binance-cost-pct", type=float, default=0.10)
    parser.add_argument("--mb-cost-pct", type=float, default=0.15)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()
    if args.progress_every <= 0:
        parser.error("--progress-every must be positive")

    domains = [
        MarketDomain(
            domain_id="binance_btcusdt",
            exchange="binance",
            symbol="BTCUSDT",
            quote_asset="USDT",
            chunks_dir=Path(args.binance_chunks),
            round_trip_cost_pct=args.binance_cost_pct,
        ),
        MarketDomain(
            domain_id="mercado_bitcoin_btcbrl",
            exchange="mercado_bitcoin",
            symbol="BTCBRL",
            quote_asset="BRL",
            chunks_dir=Path(args.mb_chunks),
            round_trip_cost_pct=args.mb_cost_pct,
        ),
    ]

    def progress(domain, index, total, path, rows):
        if index == 1 or index == total or index % args.progress_every == 0:
            print(f"[{domain.domain_id} {index}/{total}] {path.name}: {rows} eligible rows", flush=True)

    manifest = compile_market_domains(
        domains,
        Path(args.output_dir),
        base_config=DatasetConfig(),
        progress=progress,
    )
    for domain in manifest["domains"]:
        print(
            f"[DONE] {domain['domain_metadata']['domain_id']}: {domain['rows']} rows "
            f"labels={domain['label_distribution']}"
        )
    print(f"Manifest: {(Path(args.output_dir) / 'manifest.json').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
