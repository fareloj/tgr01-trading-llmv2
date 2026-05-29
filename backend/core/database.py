import sqlite3
from pathlib import Path

# Define DB path na raiz do backend.
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = (BASE_DIR / "trading_v2.db").resolve()

REQUIRED_TABLES = {
    "klines",
    "news",
    "trade_logs",
    "virtual_portfolio",
    "system_health",
}


def get_db_path() -> Path:
    """Returns the canonical SQLite database path used by all workers."""
    return DB_PATH


def get_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Creates the necessary tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(asset, timeframe, timestamp)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            headline TEXT NOT NULL,
            headline_hash TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            is_processed BOOLEAN DEFAULT 0,
            processed_at INTEGER DEFAULT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            llm_action TEXT,
            llm_reasoning TEXT,
            action TEXT NOT NULL,
            llm_conviction REAL,
            system_reliability REAL,
            final_confidence REAL,
            executed_size REAL,
            execution_price REAL,
            reasoning TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS virtual_portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT UNIQUE NOT NULL,
            amount REAL NOT NULL
        )
    ''')

    cursor.execute('INSERT OR IGNORE INTO virtual_portfolio (currency, amount) VALUES ("BRL", 10000.0)')
    cursor.execute('INSERT OR IGNORE INTO virtual_portfolio (currency, amount) VALUES ("BTC", 0.0)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_health (
            worker_name TEXT PRIMARY KEY,
            last_heartbeat INTEGER NOT NULL
        )
    ''')

    conn.commit()
    conn.close()
    print(f"[OK] Banco de dados inicializado em {DB_PATH}")


def get_db_diagnostics() -> dict:
    """Collects SQLite health data for preflight/debug output."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row["name"] for row in cursor.fetchall()}
    missing_tables = sorted(REQUIRED_TABLES - existing_tables)

    diagnostics = {
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "tables": sorted(existing_tables),
        "missing_tables": missing_tables,
        "counts": {},
        "kline_groups": [],
        "system_health": [],
    }

    for table in sorted(REQUIRED_TABLES):
        if table not in existing_tables:
            diagnostics["counts"][table] = None
            continue
        cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
        diagnostics["counts"][table] = cursor.fetchone()["count"]

    if "klines" in existing_tables:
        cursor.execute("""
            SELECT asset, timeframe, COUNT(*) AS count,
                   MIN(timestamp) AS min_timestamp,
                   MAX(timestamp) AS max_timestamp
            FROM klines
            GROUP BY asset, timeframe
            ORDER BY count DESC
        """)
        diagnostics["kline_groups"] = [dict(row) for row in cursor.fetchall()]

    if "system_health" in existing_tables:
        cursor.execute("SELECT worker_name, last_heartbeat FROM system_health ORDER BY worker_name")
        diagnostics["system_health"] = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return diagnostics


def print_db_diagnostics():
    """Prints a compact database preflight report."""
    diagnostics = get_db_diagnostics()
    print(f"[DB] Path: {diagnostics['db_path']}")
    print(f"[DB] Size: {diagnostics['db_size_bytes']} bytes")
    if diagnostics["missing_tables"]:
        print(f"[DB] Missing tables: {', '.join(diagnostics['missing_tables'])}")
    print(f"[DB] Counts: {diagnostics['counts']}")
    if diagnostics["kline_groups"]:
        print("[DB] Klines:")
        for group in diagnostics["kline_groups"]:
            print(
                "     "
                f"{group['asset']} {group['timeframe']} "
                f"count={group['count']} "
                f"range={group['min_timestamp']}..{group['max_timestamp']}"
            )
    if diagnostics["system_health"]:
        print(f"[DB] Worker health: {diagnostics['system_health']}")


if __name__ == "__main__":
    init_db()
    print_db_diagnostics()
