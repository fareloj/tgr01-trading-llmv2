import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"

conn = sqlite3.connect(DB_PATH)
try:
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trade_logs")
    print(cursor.fetchone()[0])
finally:
    conn.close()
