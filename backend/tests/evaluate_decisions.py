import argparse
import json
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"
REPORTS_DIR = BACKEND_DIR / "reports"


def parse_horizons(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def fetch_rows(cursor, query: str, params: tuple = ()):
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def fetch_future_price(cursor, timestamp: int, horizon_minutes: int) -> dict | None:
    target = timestamp + (horizon_minutes * 60)
    cursor.execute(
        """
        SELECT timestamp, close
        FROM klines
        WHERE asset='BTC/BRL'
          AND timeframe='1m'
          AND timestamp >= ?
        ORDER BY timestamp ASC
        LIMIT 1
        """,
        (target,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def classify(action: str, move_pct: float, threshold_pct: float) -> str:
    if action == "BUY":
        if move_pct >= threshold_pct:
            return "good"
        if move_pct <= -threshold_pct:
            return "bad"
        return "neutral"

    if action == "SELL":
        if move_pct <= -threshold_pct:
            return "good"
        if move_pct >= threshold_pct:
            return "bad"
        return "neutral"

    if action == "HOLD":
        if move_pct >= threshold_pct:
            return "missed_upside"
        if move_pct <= -threshold_pct:
            return "avoided_downside"
        return "good"

    return "not_applicable"


def evaluate(db_path: Path, since_id: int | None, horizons: list[int], threshold_pct: float, limit: int):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    where = "WHERE id >= ?" if since_id is not None else ""
    params = (since_id,) if since_id is not None else ()
    rows = fetch_rows(
        cursor,
        f"""
        SELECT id, timestamp, llm_action, llm_reasoning, action, llm_conviction,
               system_reliability, final_confidence, executed_size, execution_price, reasoning
        FROM trade_logs
        {where}
        ORDER BY id ASC
        """,
        params,
    )

    evaluations = []
    summary = {
        str(horizon): {
            "matured": 0,
            "not_matured": 0,
            "good": 0,
            "bad": 0,
            "neutral": 0,
            "missed_upside": 0,
            "avoided_downside": 0,
            "not_applicable": 0,
        }
        for horizon in horizons
    }

    for row in rows:
        base_price = float(row["execution_price"] or 0.0)
        item = dict(row)
        item["horizons"] = {}

        for horizon in horizons:
            bucket = summary[str(horizon)]
            future = fetch_future_price(cursor, int(row["timestamp"]), horizon)
            if future is None or base_price <= 0:
                bucket["not_matured"] += 1
                item["horizons"][str(horizon)] = {"status": "not_matured"}
                continue

            future_price = float(future["close"])
            move_pct = ((future_price - base_price) / base_price) * 100.0
            result = classify(row["action"], move_pct, threshold_pct)
            bucket["matured"] += 1
            bucket[result] += 1
            item["horizons"][str(horizon)] = {
                "status": result,
                "future_timestamp": int(future["timestamp"]),
                "future_price": round(future_price, 2),
                "move_pct": round(move_pct, 4),
            }

        evaluations.append(item)

    conn.close()

    report = {
        "db_path": str(db_path.resolve()),
        "since_id": since_id,
        "threshold_pct": threshold_pct,
        "horizons_minutes": horizons,
        "logs_evaluated": len(rows),
        "summary": summary,
        "evaluations": evaluations,
    }

    print_report(report, limit=limit)
    return report


def print_report(report: dict, limit: int):
    print(f"DB: {report['db_path']}")
    if report["since_id"] is not None:
        print(f"Filtro: trade_logs.id >= {report['since_id']}")
    print(f"Threshold: +/-{report['threshold_pct']}%")
    print(f"Logs avaliados: {report['logs_evaluated']}")

    print("\nResumo por horizonte")
    for horizon, data in report["summary"].items():
        print(f"  {horizon}m: {data}")

    print("\nExemplos")
    for item in report["evaluations"][-limit:]:
        print(
            f"  id={item['id']} action={item['action']} llm={item['llm_action']} "
            f"price={item['execution_price']} reason={item['reasoning']}"
        )
        for horizon, result in item["horizons"].items():
            print(f"    {horizon}m -> {result}")


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate decisions against future BTC/BRL price movement.")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB path.")
    parser.add_argument("--since-id", type=int, default=None, help="Only evaluate trade_logs with id >= this value.")
    parser.add_argument("--horizons", default="5,15,30,60", help="Comma-separated horizons in minutes.")
    parser.add_argument("--threshold", type=float, default=0.20, help="Opportunity threshold in percent. Default: 0.20")
    parser.add_argument("--limit", type=int, default=10, help="Example rows to print. Default: 10")
    parser.add_argument("--json-out", default="", help="Optional JSON report output path.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = evaluate(
        db_path=Path(args.db),
        since_id=args.since_id,
        horizons=parse_horizons(args.horizons),
        threshold_pct=args.threshold,
        limit=args.limit,
    )
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON salvo em: {output.resolve()}")
