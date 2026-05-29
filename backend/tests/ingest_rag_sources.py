import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from core.database import get_connection
from rag.rag_store import init_rag_tables, upsert_document


DEFAULT_PROJECT_DOCS = [
    ("README.md", "architecture_note"),
    ("project_super_summary.md", "architecture_note"),
    ("crypto_study_plan_for_tgr01.md", "study_note"),
    ("rag_agent_context_design.md", "architecture_note"),
    ("final_architecture_review.md", "architecture_note"),
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
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, headline, source
            FROM news
            WHERE timestamp >= strftime('%s', 'now') - ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (hours * 3600, limit),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

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
    parser.add_argument("--project-docs", action="store_true", help="Ingest README, study plan and architecture notes.")
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
