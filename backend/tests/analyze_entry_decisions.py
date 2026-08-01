import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import trade_logs
from backend.tests.evaluate_decisions import (
    DEFAULT_MAX_CANDLE_DELAY_SECONDS,
    assess_future_price,
    classify,
    parse_horizons,
)

def load_snapshot(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def entry_kind(row: dict) -> str:
    if row["action"] in {"BUY", "SELL"}:
        return "approved"
    if row["llm_action"] in {"BUY", "SELL"}:
        return "blocked"
    return "ignored"


def evaluation_base_price(row: dict) -> float:
    if row["action"] in {"BUY", "SELL"} and row.get("effective_price"):
        return float(row["effective_price"])
    return float(row["execution_price"] or 0.0)


def evaluate_entries(
    since_id: int | None,
    horizons: list[int],
    threshold_pct: float,
    max_candle_delay_seconds: int = DEFAULT_MAX_CANDLE_DELAY_SECONDS,
) -> dict:
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
        trade_logs.c.payload_snapshot_json,
    )
    if since_id is not None:
        stmt = stmt.where(trade_logs.c.id >= since_id)
    stmt = stmt.order_by(trade_logs.c.id.asc())

    entries = []
    with database.engine.connect() as conn:
        rows = [dict(row._mapping) for row in conn.execute(stmt)]
        for row in rows:
            kind = entry_kind(row)
            if kind == "ignored":
                continue
            snapshot = load_snapshot(row.pop("payload_snapshot_json", None))
            technical = snapshot.get("technical", {})
            item = dict(row)
            item["kind"] = kind
            item["evaluation_base_price"] = evaluation_base_price(row)
            item["technical"] = technical
            item["news_risk"] = snapshot.get("news_risk", {})
            item["data_health"] = snapshot.get("data_health", {})
            item["horizons"] = {}
            for horizon in horizons:
                maturity, future = assess_future_price(
                    conn,
                    int(row["timestamp"]),
                    horizon,
                    max_candle_delay_seconds,
                )
                base_price = item["evaluation_base_price"]
                if not base_price:
                    item["horizons"][str(horizon)] = {
                        "status": "not_matured",
                        "reason": "invalid_base_price",
                    }
                    continue
                if maturity != "matured" or future is None:
                    item["horizons"][str(horizon)] = {
                        "status": maturity,
                        "target_timestamp": int(row["timestamp"]) + (horizon * 60),
                        "max_candle_delay_seconds": max_candle_delay_seconds,
                    }
                    continue
                move_pct = ((float(future["close"]) - base_price) / base_price) * 100.0
                evaluated_action = row["action"] if kind == "approved" else "HOLD"
                item["horizons"][str(horizon)] = {
                    "status": classify(evaluated_action, move_pct, threshold_pct),
                    "future_timestamp": int(future["timestamp"]),
                    "future_price": round(float(future["close"]), 2),
                    "move_pct": round(move_pct, 4),
                }
            entries.append(item)

    approved = [entry for entry in entries if entry["kind"] == "approved"]
    blocked = [entry for entry in entries if entry["kind"] == "blocked"]
    block_reasons = Counter(entry["reasoning"] for entry in blocked)
    approved_bad_15m = [
        entry for entry in approved if entry["horizons"].get("15", {}).get("status") == "bad"
    ]
    diagnostics = []
    if approved_bad_15m:
        diagnostics.append(
            {
                "code": "BUY_CONFIRMATION_WATCH",
                "severity": "WATCH",
                "message": (
                    "BUY aprovado perdeu >= threshold em 15m. "
                    "Acumule mais amostras antes de exigir confirmacao adicional."
                ),
                "count": len(approved_bad_15m),
            }
        )

    return {
        "db_path": database.get_database_label(),
        "since_id": since_id,
        "threshold_pct": threshold_pct,
        "horizons_minutes": horizons,
        "max_candle_delay_seconds": max_candle_delay_seconds,
        "entries_total": len(entries),
        "approved_count": len(approved),
        "blocked_count": len(blocked),
        "blocked_reasons": dict(block_reasons),
        "diagnostics": diagnostics,
        "entries": entries,
    }


def print_report(report: dict):
    print(f"DB: {report['db_path']}")
    print(f"Filtro: trade_logs.id >= {report['since_id']}")
    print(f"Entradas: approved={report['approved_count']} blocked={report['blocked_count']}")
    print("\nMotivos de bloqueio")
    for reason, count in report["blocked_reasons"].items():
        print(f"  {count}x {reason}")
    print("\nDiagnosticos")
    if not report["diagnostics"]:
        print("  (empty)")
    for item in report["diagnostics"]:
        print(f"  [{item['severity']}] {item['code']}: {item['message']} count={item['count']}")
    print("\nEntradas detalhadas")
    for item in report["entries"]:
        technical = item["technical"]
        print(
            f"  id={item['id']} kind={item['kind']} llm={item['llm_action']} final={item['action']} "
            f"price={item.get('evaluation_base_price', item['execution_price'])} rsi={technical.get('rsi_value')} {technical.get('rsi_status')} "
            f"macd={technical.get('macd_status')} reason={item['reasoning']}"
        )
        for horizon, result in item["horizons"].items():
            print(f"    {horizon}m -> {result}")


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze approved and blocked entry decisions.")
    parser.add_argument("--since-id", type=int, default=None)
    parser.add_argument("--horizons", default="5,15,30,60")
    parser.add_argument("--threshold", type=float, default=0.20)
    parser.add_argument("--max-candle-delay", type=int, default=DEFAULT_MAX_CANDLE_DELAY_SECONDS)
    parser.add_argument("--json-out", default="")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = evaluate_entries(
        args.since_id,
        parse_horizons(args.horizons),
        args.threshold,
        args.max_candle_delay,
    )
    print_report(report)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON salvo em: {output.resolve()}")
