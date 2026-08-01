from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from backend.data.market_history import write_chunk_atomic
from backend.ml.dataset import DatasetConfig
from backend.ml.multidomain import MarketDomain, compile_market_domains


def make_domain_chunks(path: Path, *, start: int, symbol: str, source: str) -> None:
    path.mkdir(parents=True)
    rows = []
    for index in range(600):
        close = 100.0 + index * 0.01 + math.sin(index / 13.0) * 0.3
        rows.append(
            {
                "timestamp": start + index * 60,
                "datetime_utc": "",
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 10.0 + index % 5,
                "symbol": symbol,
                "resolution": "1m",
                "source": source,
            }
        )
    write_chunk_atomic(path / f"{rows[0]['timestamp']}_{rows[-1]['timestamp']}.csv", rows)


def test_multidomain_compiler_keeps_markets_in_separate_files(tmp_path: Path):
    global_chunks = tmp_path / "global"
    local_chunks = tmp_path / "local"
    make_domain_chunks(global_chunks, start=1_700_000_000, symbol="BTCUSDT", source="binance")
    make_domain_chunks(local_chunks, start=1_800_000_000, symbol="BTC-BRL", source="mb")
    domains = [
        MarketDomain("binance_btcusdt", "binance", "BTCUSDT", "USDT", global_chunks, 0.10),
        MarketDomain("mercado_bitcoin_btcbrl", "mercado_bitcoin", "BTCBRL", "BRL", local_chunks, 0.15),
    ]

    manifest = compile_market_domains(domains, tmp_path / "output", base_config=DatasetConfig())
    global_frame = pd.read_csv(tmp_path / "output" / "binance_btcusdt.csv")
    local_frame = pd.read_csv(tmp_path / "output" / "mercado_bitcoin_btcbrl.csv")

    assert manifest["training_order"] == ["binance_btcusdt", "mercado_bitcoin_btcbrl"]
    assert set(global_frame["domain_id"]) == {"binance_btcusdt"}
    assert set(local_frame["domain_id"]) == {"mercado_bitcoin_btcbrl"}
    assert set(global_frame["quote_asset"]) == {"USDT"}
    assert set(local_frame["quote_asset"]) == {"BRL"}
    assert not (tmp_path / "output" / "combined.csv").exists()
