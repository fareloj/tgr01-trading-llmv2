import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from rag.rag_store import build_context_block, search_chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the optional RAG store.")
    parser.add_argument("query")
    parser.add_argument("--source-type", action="append", dest="source_types")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-age-hours", type=int)
    parser.add_argument("--purpose", default="manual_query")
    args = parser.parse_args()

    max_age_seconds = args.max_age_hours * 3600 if args.max_age_hours else None
    chunks = search_chunks(
        args.query,
        source_types=args.source_types,
        limit=args.limit,
        max_age_seconds=max_age_seconds,
        purpose=args.purpose,
    )
    print(build_context_block(chunks, title="RAG QUERY RESULT"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
