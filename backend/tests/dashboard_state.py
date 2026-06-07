import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"
REPORTS_DIR = BACKEND_DIR / "reports"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.clock_sync import check_clock_skew


def parse_snapshot(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def fetch_dashboard_state(db_path: Path, recent_limit: int = 12) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    now = int(time.time())

    workers = {}
    for row in cursor.execute("SELECT worker_name, last_heartbeat FROM system_health ORDER BY worker_name"):
        age = now - int(row["last_heartbeat"])
        limit = 300 if row["worker_name"] == "price_worker" else 3600
        workers[row["worker_name"]] = {
            "last_heartbeat": int(row["last_heartbeat"]),
            "age_seconds": age,
            "status": "healthy" if age <= limit else "stale",
        }

    latest_kline = cursor.execute(
        """
        SELECT timestamp, close
        FROM klines
        WHERE asset='BTC/BRL' AND timeframe='1m'
        ORDER BY timestamp DESC
        LIMIT 1
        """
    ).fetchone()
    latest_news = cursor.execute(
        "SELECT timestamp, source, headline FROM news ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    portfolio_rows = cursor.execute("SELECT currency, amount FROM virtual_portfolio").fetchall()
    portfolio = {row["currency"]: float(row["amount"]) for row in portfolio_rows}
    table_names = {
        row["name"]
        for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    rag = {"documents": 0, "chunks": 0, "retrievals": 0}
    if "rag_documents" in table_names:
        rag["documents"] = int(cursor.execute("SELECT COUNT(*) FROM rag_documents").fetchone()[0])
    if "rag_chunks" in table_names:
        rag["chunks"] = int(cursor.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0])
    if "rag_retrieval_logs" in table_names:
        rag["retrievals"] = int(cursor.execute("SELECT COUNT(*) FROM rag_retrieval_logs").fetchone()[0])

    trade_log_columns = table_columns(cursor, "trade_logs")
    base_log_columns = [
        "id",
        "timestamp",
        "llm_action",
        "llm_reasoning",
        "action",
        "llm_conviction",
        "system_reliability",
        "final_confidence",
        "executed_size",
        "execution_price",
        "reasoning",
        "payload_snapshot_json",
    ]
    optional_execution_columns = [
        "llm_decision_brief",
        "fee_rate",
        "fee_brl",
        "slippage_rate",
        "expected_price",
        "effective_price",
        "gross_notional_brl",
        "net_notional_brl",
        "brl_delta",
        "btc_delta",
        "equity_before_brl",
        "equity_after_brl",
        "realized_pnl_brl",
        "position_avg_cost_brl",
    ]
    selected_log_columns = base_log_columns + [
        column for column in optional_execution_columns if column in trade_log_columns
    ]

    logs = []
    for row in cursor.execute(
        f"""
        SELECT {", ".join(selected_log_columns)}
        FROM trade_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (recent_limit,),
    ):
        item = {key: row[key] for key in row.keys() if key != "payload_snapshot_json"}
        item["snapshot"] = parse_snapshot(row["payload_snapshot_json"])
        logs.append(item)

    latest_price = float(latest_kline["close"]) if latest_kline else 0.0
    equity = portfolio.get("BRL", 0.0) + portfolio.get("BTC", 0.0) * latest_price
    exposure = (portfolio.get("BTC", 0.0) * latest_price / equity * 100.0) if equity else 0.0
    conn.close()
    clock = check_clock_skew(timeout=2.0)
    reports = []
    if REPORTS_DIR.exists():
        for path in sorted(REPORTS_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)[:8]:
            if path.is_file():
                stat = path.stat()
                reports.append(
                    {
                        "name": path.name,
                        "size_bytes": stat.st_size,
                        "modified_at": int(stat.st_mtime),
                    }
                )

    return {
        "generated_at": now,
        "db_path": str(db_path.resolve()),
        "clock": clock,
        "workers": workers,
        "latest_kline": {
            "timestamp": int(latest_kline["timestamp"]) if latest_kline else None,
            "age_seconds": now - int(latest_kline["timestamp"]) if latest_kline else None,
            "close": latest_price,
        },
        "latest_news": {
            "timestamp": int(latest_news["timestamp"]) if latest_news else None,
            "age_seconds": now - int(latest_news["timestamp"]) if latest_news else None,
            "source": latest_news["source"] if latest_news else None,
            "headline": latest_news["headline"] if latest_news else None,
        },
        "portfolio": {
            "brl": portfolio.get("BRL", 0.0),
            "btc": portfolio.get("BTC", 0.0),
            "equity_brl": round(equity, 2),
            "exposure_pct": round(exposure, 2),
        },
        "rag": rag,
        "reports": reports,
        "entry_evaluation": read_json_file(REPORTS_DIR / "last_entry_decisions.json"),
        "logs": logs,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Emit operational dashboard state as JSON.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--recent-limit", type=int, default=12)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fetch_dashboard_state(Path(args.db), recent_limit=args.recent_limit), ensure_ascii=False))
