"""Print a compact end-to-end audit of recent trading cycles."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import repository
from backend.core.database import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze persisted trading run lifecycle records.")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit deve estar entre 1 e 1000")

    init_db()
    rows = repository.get_trading_runs(limit=args.limit)
    print(f"Runs encontradas: {len(rows)}")
    for row in rows:
        started = datetime.fromtimestamp(row["started_at"]).isoformat(sep=" ", timespec="seconds")
        print(
            f"{row['run_id']} | {started} | {row['status']}/{row['stage']} | "
            f"LLM={row['llm_action'] or 'SKIPPED'} "
            f"Risk={row['risk_action'] or '-'} | {row['duration_ms'] or 0:.1f}ms | "
            f"trade_log={row['trade_log_id'] or '-'}"
        )
        if row.get("llm_reasoning"):
            print(f"  LLM: {row['llm_reasoning']}")
        if row.get("risk_reason"):
            print(f"  Risk: {row['risk_reason']}")
        if row.get("execution_audit_json"):
            execution = json.loads(row["execution_audit_json"])
            print(
                "  Exec: "
                f"fee={execution.get('fee_brl')} "
                f"slippage={execution.get('slippage_rate')} "
                f"brl_delta={execution.get('brl_delta')} "
                f"btc_delta={execution.get('btc_delta')}"
            )
        if row.get("error_type"):
            print(f"  Error: {row['error_type']}: {row.get('error_message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
