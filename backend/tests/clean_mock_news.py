import argparse
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"

MOCK_SOURCES = {"Bloomberg", "CryptoPanic", "Exame", "InfoMoney"}
REAL_SOURCES = {"CoinDesk", "Cointelegraph", "Decrypt"}


def find_mock_news(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in MOCK_SOURCES)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT id, timestamp, source, headline
        FROM news
        WHERE source IN ({placeholders})
        ORDER BY timestamp DESC
        """,
        tuple(sorted(MOCK_SOURCES)),
    )
    return cursor.fetchall()


def summarize_real_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in REAL_SOURCES)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT source, COUNT(*) AS count, MIN(timestamp) AS min_timestamp, MAX(timestamp) AS max_timestamp
        FROM news
        WHERE source IN ({placeholders})
        GROUP BY source
        ORDER BY source
        """,
        tuple(sorted(REAL_SOURCES)),
    )
    return cursor.fetchall()


def clean_mock_news(db_path: Path, apply: bool):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = find_mock_news(conn)
        print(f"DB: {db_path.resolve()}")
        print(f"Mock news candidates: {len(rows)}")

        for row in rows[:20]:
            print(f"  id={row['id']} ts={row['timestamp']} source={row['source']} headline={row['headline'][:100]}")
        if len(rows) > 20:
            print(f"  ... {len(rows) - 20} more")

        print("\nReal sources preserved:")
        for row in summarize_real_sources(conn):
            print(dict(row))

        if not apply:
            print("\nDRY RUN only. Re-run with --apply to delete mock-source rows.")
            return

        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in MOCK_SOURCES)
        cursor.execute(f"DELETE FROM news WHERE source IN ({placeholders})", tuple(sorted(MOCK_SOURCES)))
        conn.commit()
        print(f"\nDeleted mock news rows: {cursor.rowcount}")
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Remove clearly mocked news rows while preserving real RSS sources.")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB path. Default: backend/trading_v2.db")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="List rows that would be removed.")
    group.add_argument("--apply", action="store_true", help="Delete mock-source rows.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    clean_mock_news(Path(args.db), apply=args.apply)
