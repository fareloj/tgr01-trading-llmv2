import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from features.payload_builder import build_agent_payload
from rag.decision_memory import build_decision_context_block, build_trade_log_context_block


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve RAG memory for a current payload or trade_log.")
    parser.add_argument("--trade-log-id", type=int, help="Retrieve memory for a specific trade_logs.id.")
    parser.add_argument("--current-payload", action="store_true", help="Retrieve memory for the current market payload.")
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()

    if args.trade_log_id is None and not args.current_payload:
        parser.error("Use --trade-log-id ID or --current-payload")

    if args.trade_log_id is not None:
        print(build_trade_log_context_block(args.trade_log_id, limit=args.limit))
    else:
        payload = build_agent_payload()
        if payload.get("status") == "ERROR":
            raise RuntimeError(payload.get("message", "payload error"))
        print(build_decision_context_block(payload, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
