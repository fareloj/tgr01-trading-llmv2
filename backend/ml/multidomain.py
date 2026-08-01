from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.ml.chunked import ProgressCallback, build_dataset_from_chunks
from backend.ml.dataset import DatasetConfig


@dataclass(frozen=True)
class MarketDomain:
    domain_id: str
    exchange: str
    symbol: str
    quote_asset: str
    chunks_dir: Path
    round_trip_cost_pct: float

    def __post_init__(self) -> None:
        identifiers = (self.domain_id, self.exchange, self.symbol, self.quote_asset)
        if any(not value or not value.replace("_", "").replace("-", "").isalnum() for value in identifiers):
            raise ValueError("domain identifiers must be non-empty alphanumeric names")
        if self.round_trip_cost_pct < 0:
            raise ValueError("domain round-trip cost must be non-negative")

    def metadata(self) -> dict[str, str]:
        return {
            "domain_id": self.domain_id,
            "exchange": self.exchange,
            "market_symbol": self.symbol,
            "quote_asset": self.quote_asset,
        }


DomainProgress = Callable[[MarketDomain, int, int, Path, int], None]


def compile_market_domains(
    domains: list[MarketDomain],
    output_dir: Path,
    *,
    base_config: DatasetConfig | None = None,
    progress: DomainProgress | None = None,
) -> dict:
    if not domains:
        raise ValueError("at least one market domain is required")
    ids = [domain.domain_id for domain in domains]
    if len(ids) != len(set(ids)):
        raise ValueError("market domain ids must be unique")

    base_config = base_config or DatasetConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for domain in domains:
        config = replace(base_config, round_trip_cost_pct=domain.round_trip_cost_pct)
        callback: ProgressCallback | None = None
        if progress:
            callback = lambda index, total, path, rows, current=domain: progress(
                current, index, total, path, rows
            )
        summary = build_dataset_from_chunks(
            domain.chunks_dir,
            output_dir / f"{domain.domain_id}.csv",
            config=config,
            metadata_path=output_dir / f"{domain.domain_id}.json",
            progress=callback,
            domain_metadata=domain.metadata(),
        )
        summaries.append(summary)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "training_order": [domain.domain_id for domain in domains],
        "boundary": (
            "Domains are compiled independently. Pretrain on the global domain, then fine-tune and "
            "calibrate on the local execution domain. Do not randomly split a concatenated dataset."
        ),
        "domains": summaries,
    }
    path = output_dir / "manifest.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return manifest
