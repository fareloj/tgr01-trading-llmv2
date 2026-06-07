import argparse
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core.database import get_db_path

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class MarketWindow:
    label: str
    start_ts: int
    end_ts: int
    start_price: float
    end_price: float
    move_pct: float
    volatility_pct: float
    candles: int


def parse_local_datetime(value: str) -> int:
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return int(dt.replace(tzinfo=LOCAL_TZ).timestamp())


def format_local(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def fetch_candles(db_path: Path, asset: str, timeframe: str, from_ts: int, to_ts: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM klines
            WHERE asset = ? AND timeframe = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
            """,
            (asset, timeframe, from_ts, to_ts),
        ).fetchall()
    finally:
        conn.close()


def classify_move(move_pct: float, *, trend_threshold_pct: float, sideways_threshold_pct: float) -> str:
    if move_pct >= trend_threshold_pct:
        return "UPTREND"
    if move_pct <= -trend_threshold_pct:
        return "DOWNTREND"
    if abs(move_pct) <= sideways_threshold_pct:
        return "SIDEWAYS"
    return "MIXED"


def window_volatility_pct(rows: list[sqlite3.Row]) -> float:
    prices = [float(row["close"]) for row in rows]
    if len(prices) < 2:
        return 0.0
    returns = []
    for previous, current in zip(prices, prices[1:]):
        if previous:
            returns.append((current - previous) / previous * 100.0)
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / len(returns)
    return round(math.sqrt(variance), 4)


def find_windows(
    rows: list[sqlite3.Row],
    *,
    window_minutes: int,
    stride_minutes: int,
    trend_threshold_pct: float,
    sideways_threshold_pct: float,
    min_coverage_pct: float,
) -> list[MarketWindow]:
    if not rows:
        return []

    by_ts = {int(row["timestamp"]): row for row in rows}
    timestamps = sorted(by_ts)
    first_ts = timestamps[0]
    last_ts = timestamps[-1]
    window_seconds = window_minutes * 60
    stride_seconds = stride_minutes * 60
    min_candles = max(2, int(window_minutes * (min_coverage_pct / 100.0)))

    windows = []
    start_ts = first_ts
    while start_ts + window_seconds <= last_ts:
        end_ts = start_ts + window_seconds
        window_rows = [by_ts[ts] for ts in timestamps if start_ts <= ts <= end_ts]
        if len(window_rows) >= min_candles:
            start_price = float(window_rows[0]["close"])
            end_price = float(window_rows[-1]["close"])
            move_pct = round(((end_price - start_price) / start_price * 100.0), 4) if start_price else 0.0
            label = classify_move(
                move_pct,
                trend_threshold_pct=trend_threshold_pct,
                sideways_threshold_pct=sideways_threshold_pct,
            )
            windows.append(
                MarketWindow(
                    label=label,
                    start_ts=int(window_rows[0]["timestamp"]),
                    end_ts=int(window_rows[-1]["timestamp"]),
                    start_price=start_price,
                    end_price=end_price,
                    move_pct=move_pct,
                    volatility_pct=window_volatility_pct(window_rows),
                    candles=len(window_rows),
                )
            )
        start_ts += stride_seconds
    return windows


def select_examples(windows: list[MarketWindow], per_label: int) -> list[MarketWindow]:
    selected = []
    up = [item for item in windows if item.label == "UPTREND"]
    down = [item for item in windows if item.label == "DOWNTREND"]
    sideways = [item for item in windows if item.label == "SIDEWAYS"]

    selected.extend(sorted(up, key=lambda item: item.move_pct, reverse=True)[:per_label])
    selected.extend(sorted(down, key=lambda item: item.move_pct)[:per_label])
    selected.extend(sorted(sideways, key=lambda item: (abs(item.move_pct), item.volatility_pct))[:per_label])
    return selected


def print_windows(title: str, windows: list[MarketWindow]) -> None:
    print(f"\n{title}")
    if not windows:
        print("  (empty)")
        return
    for item in windows:
        print(
            f"  {item.label:<9} {format_local(item.start_ts)} -> {format_local(item.end_ts)} "
            f"move={item.move_pct:+.4f}% vol={item.volatility_pct:.4f}% "
            f"candles={item.candles} price={item.start_price:.2f}->{item.end_price:.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Find uptrend/downtrend/sideways BTC windows already stored in SQLite.")
    parser.add_argument("--db", default=str(get_db_path()))
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--from-local", required=True, help='Start datetime, format "YYYY-MM-DD HH:MM".')
    parser.add_argument("--to-local", required=True, help='End datetime, format "YYYY-MM-DD HH:MM".')
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--stride-minutes", type=int, default=10)
    parser.add_argument("--trend-threshold-pct", type=float, default=0.5)
    parser.add_argument("--sideways-threshold-pct", type=float, default=0.15)
    parser.add_argument("--min-coverage-pct", type=float, default=80.0)
    parser.add_argument("--per-label", type=int, default=5)
    args = parser.parse_args()

    rows = fetch_candles(
        Path(args.db),
        args.asset,
        args.timeframe,
        parse_local_datetime(args.from_local),
        parse_local_datetime(args.to_local),
    )
    windows = find_windows(
        rows,
        window_minutes=args.window_minutes,
        stride_minutes=args.stride_minutes,
        trend_threshold_pct=args.trend_threshold_pct,
        sideways_threshold_pct=args.sideways_threshold_pct,
        min_coverage_pct=args.min_coverage_pct,
    )

    print(f"DB: {Path(args.db).resolve()}")
    print(f"Candles na janela: {len(rows)}")
    print(f"Janelas avaliadas: {len(windows)}")
    print_windows("Melhores exemplos por regime", select_examples(windows, args.per_label))
    print_windows(
        "Resumo ordenado por inicio",
        sorted(select_examples(windows, args.per_label), key=lambda item: item.start_ts),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
