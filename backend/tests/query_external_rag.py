"""Inspect the external hybrid RAG without granting it trading authority."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.rag.external_client import ExternalRagClient, build_untrusted_context


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Consulta observacional ao RAG hibrido externo.")
    parser.add_argument("query", nargs="?", default="risk manager stale market data safeguards")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--path-prefix")
    parser.add_argument("--language")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--no-reranker", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    client = ExternalRagClient()
    health = client.health()
    if args.health:
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return 0 if health.get("status") == "ready" else 2

    print(
        "External RAG: "
        f"{health.get('status', 'unknown')} | "
        f"dense={health.get('dense_indexed', 0)} | "
        f"lexical={health.get('lexical_indexed', 0)} | "
        f"reranker={health.get('reranker_device') or 'unknown'}"
    )
    result = client.search(
        args.query,
        top_k=args.top_k,
        path_prefix=args.path_prefix,
        language=args.language,
        use_reranker=not args.no_reranker,
        purpose="operator_external_rag_query",
    )
    if args.as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(build_untrusted_context(result))
        print(
            f"latency_ms={result.latency_ms} results={len(result.results)} "
            f"rejected={result.rejected_results} error={result.error or 'none'}"
        )
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
