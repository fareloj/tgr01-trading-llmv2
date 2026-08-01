from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.data.market_history import (
    DownloadConfig,
    MercadoBitcoinHistoryClient,
    align_minute,
    inspect_chunk,
    iter_download_windows,
    merge_chunks_atomic,
    write_chunk_atomic,
    write_manifest_atomic,
)


DEFAULT_OUTPUT_DIR = PROJECT_DIR / "backend" / "data_exports" / "mercado_bitcoin_btc_brl_1m"
DISCOVERY_START = int(datetime(2011, 1, 1, tzinfo=timezone.utc).timestamp())


def parse_utc(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        if normalized.isdigit():
            return int(normalized)
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use ISO UTC datetime, YYYY-MM-DD, or Unix timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def main() -> int:
    parser = argparse.ArgumentParser(description="Download resumable BTC/BRL 1m history from Mercado Bitcoin.")
    parser.add_argument("--from-utc", help="Default: discover first available daily BTC-BRL candle.")
    parser.add_argument("--to-utc", help="Default: latest fully closed minute.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument("--max-chunks", type=int, default=None, help="Bound requests for smoke tests.")
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--force", action="store_true", help="Redownload valid existing chunks.")
    parser.add_argument("--no-merge", action="store_true")
    args = parser.parse_args()
    if args.max_chunks is not None and args.max_chunks <= 0:
        parser.error("--max-chunks must be positive")
    if args.progress_every <= 0:
        parser.error("--progress-every must be positive")

    config = DownloadConfig(chunk_days=args.chunk_days)
    client = MercadoBitcoinHistoryClient(config)
    end_timestamp = align_minute(parse_utc(args.to_utc) or int(time.time())) - 60
    start_timestamp = parse_utc(args.from_utc)
    if start_timestamp is None:
        print("[DISCOVERY] Looking for earliest available BTC-BRL one-minute candle...", flush=True)
        start_timestamp = client.discover_earliest_timestamp(DISCOVERY_START, end_timestamp)
        print(
            f"[DISCOVERY] First available day: {datetime.fromtimestamp(start_timestamp, timezone.utc).isoformat()}",
            flush=True,
        )

    windows = iter_download_windows(start_timestamp, end_timestamp, config.chunk_days)
    if args.max_chunks is not None:
        windows = windows[: args.max_chunks]
    output_dir = Path(args.output_dir)
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    downloaded = 0
    resumed = 0
    started = time.monotonic()
    selected_paths = []
    for index, window in enumerate(windows, start=1):
        path = chunks_dir / window.filename
        selected_paths.append(path)
        if path.exists() and not args.force:
            try:
                inspection = inspect_chunk(path, window)
                total_rows += int(inspection["rows"])
                resumed += 1
                if index == 1 or index == len(windows) or index % args.progress_every == 0:
                    print(f"[{index}/{len(windows)}] resume {path.name}: {inspection['rows']} rows", flush=True)
                continue
            except Exception as exc:
                print(f"[{index}/{len(windows)}] invalid cached chunk, redownloading: {exc}", flush=True)

        rows = client.get_candles(window)
        written = write_chunk_atomic(path, rows)
        inspection = inspect_chunk(path, window)
        if written != inspection["rows"]:
            raise RuntimeError("chunk row count changed after atomic write")
        total_rows += written
        downloaded += 1
        elapsed = max(time.monotonic() - started, 0.001)
        rate = index / elapsed
        remaining_seconds = (len(windows) - index) / rate if rate else 0.0
        if index == 1 or index == len(windows) or index % args.progress_every == 0:
            print(
                f"[{index}/{len(windows)}] downloaded {path.name}: {written} rows | ETA {remaining_seconds / 60:.1f}m",
                flush=True,
            )

    summary = {
        "requested_start_timestamp": align_minute(start_timestamp),
        "requested_end_timestamp": end_timestamp,
        "selected_chunks": len(windows),
        "downloaded_chunks": downloaded,
        "resumed_chunks": resumed,
        "chunk_rows_total": total_rows,
        "complete_range": args.max_chunks is None,
    }
    if not args.no_merge:
        merged = merge_chunks_atomic(selected_paths, output_dir / "btc_brl_1m.csv")
        summary["merged"] = merged
        print(f"[MERGE] {merged['rows']} rows -> {merged['output_path']}", flush=True)
        print(f"[GAPS] {merged['gap_buckets']}", flush=True)

    manifest_path = output_dir / "manifest.json"
    write_manifest_atomic(manifest_path, config=config, summary=summary)
    print(f"[DONE] Manifest: {manifest_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
