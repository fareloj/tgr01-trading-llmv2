import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import klines, trade_logs


def parse_horizons(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def evaluation_base_price(row: dict) -> float:
    if row.get("action") in {"BUY", "SELL"} and row.get("effective_price"):
        return float(row["effective_price"])
    return float(row.get("execution_price") or 0.0)


def fetch_future_price(connection, timestamp: int, horizon_minutes: int) -> dict | None:
    target = timestamp + (horizon_minutes * 60)
    row = connection.execute(
        select(klines.c.timestamp, klines.c.close)
        .where(
            klines.c.asset == "BTC/BRL",
            klines.c.timeframe == "1m",
            klines.c.timestamp >= target,
        )
        .order_by(klines.c.timestamp.asc())
        .limit(1)
    ).first()
    return dict(row._mapping) if row else None


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


def evaluate(since_id: int | None, horizons: list[int], threshold_pct: float, limit: int):
    stmt = select(
        trade_logs.c.id,
        trade_logs.c.timestamp,
        trade_logs.c.llm_action,
        trade_logs.c.llm_reasoning,
        trade_logs.c.action,
        trade_logs.c.llm_conviction,
        trade_logs.c.system_reliability,
        trade_logs.c.final_confidence,
        trade_logs.c.executed_size,
        trade_logs.c.execution_price,
        trade_logs.c.effective_price,
        trade_logs.c.reasoning,
    )
    if since_id is not None:
        stmt = stmt.where(trade_logs.c.id >= since_id)
    stmt = stmt.order_by(trade_logs.c.id.asc())

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
    with database.engine.connect() as conn:
        rows = [dict(row._mapping) for row in conn.execute(stmt)]
        for row in rows:
            base_price = evaluation_base_price(row)
            item = dict(row)
            item["evaluation_base_price"] = base_price
            item["horizons"] = {}
            for horizon in horizons:
                bucket = summary[str(horizon)]
                future = fetch_future_price(conn, int(row["timestamp"]), horizon)
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

    report = {
        "db_path": database.get_database_label(),
        "since_id": since_id,
        "threshold_pct": threshold_pct,
        "horizons_minutes": horizons,
        "logs_evaluated": len(evaluations),
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
            f"price={item['evaluation_base_price']} reason={item['reasoning']}"
        )
        for horizon, result in item["horizons"].items():
            print(f"    {horizon}m -> {result}")


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate PostgreSQL decisions against future BTC/BRL movement.")
    parser.add_argument("--since-id", type=int, default=None)
    parser.add_argument("--horizons", default="5,15,30,60")
    parser.add_argument("--threshold", type=float, default=0.20)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json-out", default="")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = evaluate(
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
