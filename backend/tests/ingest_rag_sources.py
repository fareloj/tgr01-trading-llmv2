import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.core.database import get_connection
from backend.core.db_models import news
from backend.rag.rag_store import init_rag_tables, upsert_document


DEFAULT_PROJECT_DOCS = [
    ("README.md", "architecture_note"),
    ("docs/reports/FINAL_ACCEPTANCE.md", "validation_report"),
    ("docs/research/btc_trading_tools_research.md", "research_note"),
    ("backend/ml/ARCHIVED.md", "research_status"),
]


def ingest_markdown(path: Path, source_type: str, dry_run: bool) -> int | None:
    text = path.read_text(encoding="utf-8")
    if dry_run:
        print(f"[DRY] markdown {source_type}: {path}")
        return None
    return upsert_document(
        source_type=source_type,
        source=str(path.relative_to(PROJECT_DIR)),
        title=path.stem.replace("_", " "),
        text=text,
        metadata={"path": str(path.relative_to(PROJECT_DIR))},
    )


def ingest_recent_news(hours: int, limit: int, dry_run: bool) -> list[int]:
    cutoff = int(time.time()) - (hours * 3600)
    stmt = (
        select(news.c.timestamp, news.c.headline, news.c.source)
        .where(news.c.timestamp >= cutoff)
        .order_by(news.c.timestamp.desc())
        .limit(limit)
    )
    with get_connection() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings()]

    ids = []
    for row in rows:
        text = (
            f"Fonte: {row['source']}\n"
            f"Timestamp: {row['timestamp']}\n"
            f"Headline: {row['headline']}\n"
        )
        if dry_run:
            print(f"[DRY] news {row['source']} ts={row['timestamp']}: {row['headline']}")
            continue
        doc_id = upsert_document(
            source_type="news_summary",
            source=row["source"],
            title=row["headline"],
            text=text,
            published_at=int(row["timestamp"]),
            metadata={"origin_table": "news"},
        )
        ids.append(doc_id)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest curated local sources into the optional RAG store.")
    parser.add_argument(
        "--project-docs",
        action="store_true",
        help="Ingest the curated README, acceptance, trading research, and ML status documents.",
    )
    parser.add_argument("--markdown", action="append", default=[], help="Specific markdown/text file to ingest.")
    parser.add_argument("--source-type", default="study_note", help="Source type for --markdown files.")
    parser.add_argument("--news-hours", type=int, default=0, help="Ingest news from the last N hours.")
    parser.add_argument("--news-limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_rag_tables()
    inserted = []

    if args.project_docs:
        for relative_path, source_type in DEFAULT_PROJECT_DOCS:
            path = PROJECT_DIR / relative_path
            if path.exists():
                doc_id = ingest_markdown(path, source_type, args.dry_run)
                if doc_id is not None:
                    inserted.append(doc_id)
            else:
                print(f"[SKIP] Documento ausente: {path}")

    for raw_path in args.markdown:
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_DIR / path
        if not path.exists():
            raise FileNotFoundError(path)
        doc_id = ingest_markdown(path, args.source_type, args.dry_run)
        if doc_id is not None:
            inserted.append(doc_id)

    if args.news_hours > 0:
        inserted.extend(ingest_recent_news(args.news_hours, args.news_limit, args.dry_run))

    if args.dry_run:
        print("[OK] Dry-run concluido. Nada foi gravado.")
    else:
        unique_ids = sorted(set(inserted))
        print(f"[OK] RAG atualizado. Documentos tocados: {len(unique_ids)} ids={unique_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
