import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from evaluate_decisions import classify, fetch_future_price, parse_horizons

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"


def load_snapshot(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def entry_kind(row: sqlite3.Row) -> str:
    if row["action"] in {"BUY", "SELL"}:
        return "approved"
    if row["llm_action"] in {"BUY", "SELL"}:
        return "blocked"
    return "ignored"


def evaluate_entries(db_path: Path, since_id: int | None, horizons: list[int], threshold_pct: float) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    where = "WHERE id >= ?" if since_id is not None else ""
    params = (since_id,) if since_id is not None else ()
    rows = cursor.execute(
        f"""
        SELECT id, timestamp, llm_action, llm_reasoning, action, llm_conviction,
               system_reliability, final_confidence, executed_size, execution_price,
               reasoning, payload_snapshot_json
        FROM trade_logs
        {where}
        ORDER BY id ASC
        """,
        params,
    ).fetchall()

    entries = []
    for row in rows:
        kind = entry_kind(row)
        if kind == "ignored":
            continue
        snapshot = load_snapshot(row["payload_snapshot_json"])
        technical = snapshot.get("technical", {})
        item = {key: row[key] for key in row.keys() if key != "payload_snapshot_json"}
        item["kind"] = kind
        item["technical"] = technical
        item["news_risk"] = snapshot.get("news_risk", {})
        item["data_health"] = snapshot.get("data_health", {})
        item["horizons"] = {}
        for horizon in horizons:
            future = fetch_future_price(cursor, int(row["timestamp"]), horizon)
            if future is None or not row["execution_price"]:
                item["horizons"][str(horizon)] = {"status": "not_matured"}
                continue
            move_pct = ((float(future["close"]) - float(row["execution_price"])) / float(row["execution_price"])) * 100.0
            evaluated_action = row["action"] if kind == "approved" else "HOLD"
            item["horizons"][str(horizon)] = {
                "status": classify(evaluated_action, move_pct, threshold_pct),
                "future_timestamp": int(future["timestamp"]),
                "future_price": round(float(future["close"]), 2),
                "move_pct": round(move_pct, 4),
            }
        entries.append(item)
    conn.close()

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
        "db_path": str(db_path.resolve()),
        "since_id": since_id,
        "threshold_pct": threshold_pct,
        "horizons_minutes": horizons,
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
            f"price={item['execution_price']} rsi={technical.get('rsi_value')} {technical.get('rsi_status')} "
            f"macd={technical.get('macd_status')} reason={item['reasoning']}"
        )
        for horizon, result in item["horizons"].items():
            print(f"    {horizon}m -> {result}")


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze approved and blocked entry decisions.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--since-id", type=int, default=None)
    parser.add_argument("--horizons", default="5,15,30,60")
    parser.add_argument("--threshold", type=float, default=0.20)
    parser.add_argument("--json-out", default="")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = evaluate_entries(Path(args.db), args.since_id, parse_horizons(args.horizons), args.threshold)
    print_report(report)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON salvo em: {output.resolve()}")
