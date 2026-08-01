import argparse
import sys
from pathlib import Path

from sqlalchemy import delete, func, select

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import news

MOCK_SOURCES = {"Bloomberg", "CryptoPanic", "Exame", "InfoMoney"}
REAL_SOURCES = {"CoinDesk", "Cointelegraph", "Decrypt"}


def clean_mock_news(apply: bool):
    candidates_stmt = (
        select(news.c.id, news.c.timestamp, news.c.source, news.c.headline)
        .where(news.c.source.in_(sorted(MOCK_SOURCES)))
        .order_by(news.c.timestamp.desc())
    )
    real_stmt = (
        select(
            news.c.source,
            func.count().label("count"),
            func.min(news.c.timestamp).label("min_timestamp"),
            func.max(news.c.timestamp).label("max_timestamp"),
        )
        .where(news.c.source.in_(sorted(REAL_SOURCES)))
        .group_by(news.c.source)
        .order_by(news.c.source)
    )
    with database.engine.connect() as conn:
        rows = [dict(row._mapping) for row in conn.execute(candidates_stmt)]
        real_rows = [dict(row._mapping) for row in conn.execute(real_stmt)]

    print(f"DB: {database.get_database_label()}")
    print(f"Mock news candidates: {len(rows)}")
    for row in rows[:20]:
        print(f"  id={row['id']} ts={row['timestamp']} source={row['source']} headline={row['headline'][:100]}")
    if len(rows) > 20:
        print(f"  ... {len(rows) - 20} more")
    print("\nReal sources preserved:")
    for row in real_rows:
        print(row)
    if not apply:
        print("\nDRY RUN only. Re-run with --apply to delete mock-source rows.")
        return
    with database.engine.begin() as conn:
        result = conn.execute(delete(news).where(news.c.source.in_(sorted(MOCK_SOURCES))))
    print(f"\nDeleted mock news rows: {result.rowcount}")


def parse_args():
    parser = argparse.ArgumentParser(description="Remove mocked PostgreSQL news rows while preserving real RSS sources.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    clean_mock_news(apply=args.apply)
