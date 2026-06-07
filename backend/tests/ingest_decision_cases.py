import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from rag.decision_memory import upsert_recent_trade_log_cases, upsert_trade_log_case


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest trade_logs snapshots as RAG decision cases.")
    parser.add_argument("--trade-log-id", type=int, action="append", default=[])
    parser.add_argument("--since-id", type=int)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    touched = []
    for log_id in args.trade_log_id:
        touched.append(upsert_trade_log_case(log_id))

    if args.since_id is not None or not args.trade_log_id:
        touched.extend(upsert_recent_trade_log_cases(since_id=args.since_id, limit=args.limit))

    unique_ids = sorted(set(touched))
    print(f"[OK] Decision cases RAG atualizados: {len(unique_ids)} documentos ids={unique_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
