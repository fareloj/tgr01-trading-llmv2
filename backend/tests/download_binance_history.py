from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.data.binance_history import (
    ArchiveMonth,
    BinanceArchiveConfig,
    BinanceHistoryClient,
    archive_filename,
    canonical_chunk_path,
    extract_month_to_canonical_csv,
    inspect_month_chunk,
    iter_archive_months,
    previous_closed_month,
    read_month_metadata,
    write_month_metadata_atomic,
)
from backend.data.market_history import merge_chunks_atomic, write_manifest_atomic


DEFAULT_OUTPUT_DIR = PROJECT_DIR / "backend" / "data_exports" / "binance_btcusdt_1m"


def parse_month(value: str) -> ArchiveMonth:
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from exc
    return ArchiveMonth(parsed.year, parsed.month)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download checksum-verified BTCUSDT 1m monthly archives from Binance."
    )
    parser.add_argument("--from-month", type=parse_month, default=ArchiveMonth(2017, 8))
    parser.add_argument("--to-month", type=parse_month)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-months", type=int, help="Bound downloads for smoke tests.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-merge", action="store_true")
    args = parser.parse_args()
    if args.max_months is not None and args.max_months <= 0:
        parser.error("--max-months must be positive")

    output_dir = Path(args.output_dir)
    archives_dir = output_dir / "archives"
    chunks_dir = output_dir / "chunks"
    archives_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    config = BinanceArchiveConfig()
    client = BinanceHistoryClient(config)
    to_month = args.to_month
    if to_month is None:
        to_month = client.discover_latest_published_month(previous_closed_month(), archives_dir)
        print(f"[DISCOVERY] Latest published monthly archive: {to_month.key}", flush=True)
    months = iter_archive_months(args.from_month, to_month)
    if args.max_months is not None:
        months = months[: args.max_months]
    started = time.monotonic()
    downloaded = 0
    resumed = 0
    rows_total = 0
    realigned_rows_total = 0
    months_with_realignment = 0
    selected_chunks = []

    for index, month in enumerate(months, start=1):
        filename = archive_filename(config, month)
        archive_path = archives_dir / filename
        checksum_path = archives_dir / f"{filename}.CHECKSUM"
        chunk_path = canonical_chunk_path(chunks_dir, month)
        metadata_path = chunk_path.with_suffix(".metadata.json")
        selected_chunks.append(chunk_path)
        if chunk_path.exists() and not args.force:
            try:
                digest = client.ensure_month_archive(month, archive_path, checksum_path)
                inspection = inspect_month_chunk(chunk_path, month)
                metadata = read_month_metadata(
                    metadata_path,
                    month=month,
                    archive_sha256=digest,
                )
                if any(
                    metadata[field] != inspection[field]
                    for field in ("rows", "first_timestamp", "last_timestamp")
                ):
                    raise RuntimeError("month metadata does not match canonical chunk")
                rows_total += int(inspection["rows"])
                realigned_rows_total += metadata["realigned_rows"]
                months_with_realignment += int(metadata["realigned_rows"] > 0)
                resumed += 1
                print(
                    f"[{index}/{len(months)}] resume {month.key}: {inspection['rows']} rows "
                    f"realigned={metadata['realigned_rows']}",
                    flush=True,
                )
                continue
            except Exception as exc:
                print(f"[{index}/{len(months)}] invalid cache, rebuilding: {exc}", flush=True)

        digest = client.ensure_month_archive(
            month,
            archive_path,
            checksum_path,
            force=args.force,
        )
        summary = extract_month_to_canonical_csv(archive_path, chunk_path, month, config)
        write_month_metadata_atomic(
            metadata_path,
            month=month,
            archive_sha256=digest,
            extraction=summary,
        )
        rows_total += int(summary["rows"])
        realigned_rows_total += int(summary["realigned_rows"])
        months_with_realignment += int(summary["realigned_rows"] > 0)
        downloaded += 1
        elapsed = max(time.monotonic() - started, 0.001)
        eta = ((len(months) - index) / (index / elapsed)) / 60.0
        print(
            f"[{index}/{len(months)}] {month.key}: {summary['rows']} rows "
            f"realigned={summary['realigned_rows']} sha256={digest[:12]} ETA={eta:.1f}m",
            flush=True,
        )

    manifest_summary = {
        "requested_from_month": args.from_month.key,
        "requested_to_month": to_month.key,
        "selected_months": len(months),
        "downloaded_months": downloaded,
        "resumed_months": resumed,
        "chunk_rows_total": rows_total,
        "realigned_rows_total": realigned_rows_total,
        "months_with_realignment": months_with_realignment,
        "complete_range": args.max_months is None,
    }
    if not args.no_merge:
        merged = merge_chunks_atomic(selected_chunks, output_dir / "btc_usdt_1m.csv")
        manifest_summary["merged"] = merged
        print(f"[MERGE] {merged['rows']} rows -> {merged['output_path']}", flush=True)
        print(f"[GAPS] {merged['gap_buckets']}", flush=True)

    # write_manifest_atomic only requires a dataclass config and serializable summary.
    write_manifest_atomic(output_dir / "manifest.json", config=config, summary=manifest_summary)
    print(f"[DONE] {datetime.now(timezone.utc).isoformat()} | config={asdict(config)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
