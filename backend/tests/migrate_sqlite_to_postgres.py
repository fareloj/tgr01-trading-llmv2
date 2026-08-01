"""One-way recovery/import tool from the legacy SQLite file to PostgreSQL."""

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import func, select, text

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import metadata

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "trading_v2.db"
TABLE_ORDER = (
    "klines",
    "news",
    "trade_logs",
    "virtual_portfolio",
    "paper_position_state",
    "paper_position_reconciliations",
    "system_health",
    "rag_documents",
    "rag_chunks",
    "rag_retrieval_logs",
)


def _sqlite_rows(connection: sqlite3.Connection, table_name: str) -> list[dict]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    if not exists:
        return []
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table_name}"')]


def _filtered_rows(table_name: str, rows: list[dict]) -> list[dict]:
    columns = set(metadata.tables[table_name].c.keys())
    return [{key: value for key, value in row.items() if key in columns} for row in rows]


def inspect_source(source: Path) -> dict[str, int]:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite legado nao encontrado: {source}")
    uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return {name: len(_sqlite_rows(conn, name)) for name in TABLE_ORDER}


def migrate(source: Path, *, replace: bool) -> dict[str, int]:
    database.init_db()
    uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as sqlite_conn:
        sqlite_conn.row_factory = sqlite3.Row
        source_rows = {
            name: _filtered_rows(name, _sqlite_rows(sqlite_conn, name))
            for name in TABLE_ORDER
        }

    with database.engine.begin() as pg_conn:
        destination_counts = {
            name: int(pg_conn.scalar(select(func.count()).select_from(metadata.tables[name])) or 0)
            for name in TABLE_ORDER
        }
        if any(destination_counts.values()) and not replace:
            raise RuntimeError(
                "PostgreSQL de destino nao esta vazio. Use --replace somente apos conferir o backup. "
                f"Contagens atuais: {destination_counts}"
            )
        if replace:
            pg_conn.execute(
                text(f"TRUNCATE TABLE {', '.join(TABLE_ORDER)} RESTART IDENTITY CASCADE")
            )
        for name in TABLE_ORDER:
            rows = source_rows[name]
            if rows:
                pg_conn.execute(metadata.tables[name].insert(), rows)

        for name in TABLE_ORDER:
            table = metadata.tables[name]
            if "id" not in table.c or not table.c.id.primary_key:
                continue
            max_id = int(pg_conn.scalar(select(func.max(table.c.id))) or 0)
            sequence = pg_conn.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                {"table_name": name},
            )
            if sequence:
                pg_conn.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                    {"sequence": sequence, "value": max(max_id, 1), "called": max_id > 0},
                )

    with database.engine.connect() as conn:
        actual = {
            name: int(conn.scalar(select(func.count()).select_from(metadata.tables[name])) or 0)
            for name in TABLE_ORDER
        }
    expected = {name: len(rows) for name, rows in source_rows.items()}
    if actual != expected:
        raise RuntimeError(f"Contagens divergentes apos migracao. esperado={expected} atual={actual}")
    return actual


def parse_args():
    parser = argparse.ArgumentParser(description="Recover the configured PostgreSQL database from legacy SQLite.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--apply", action="store_true", help="Execute the migration; default is inspection only.")
    parser.add_argument("--replace", action="store_true", help="Replace all destination rows transactionally.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    counts = inspect_source(args.source)
    print(f"Source: {args.source.resolve()}")
    print(f"Target: {database.get_database_label()}")
    print(f"Source counts: {counts}")
    if not args.apply:
        print("DRY RUN. Use --apply; add --replace only for an intentionally replaceable target.")
    else:
        result = migrate(args.source, replace=args.replace)
        print(f"[OK] SQLite -> PostgreSQL concluido e validado: {result}")
