import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Force mock LLM before importing main.py/load_dotenv.
os.environ["GROQ_API_KEY"] = ""

from core import database


def popular_banco_fake():
    conn = database.get_connection()
    try:
        cursor = conn.cursor()

        timestamp = int(time.time()) - 3000
        price = 50000.0
        for i in range(50):
            cursor.execute(
                """
                INSERT OR IGNORE INTO klines
                    (asset, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("BTC/BRL", "1m", timestamp, price, price + 100, price - 100, price + 50, 1.0),
            )
            timestamp += 60
            price += 10

        cursor.execute(
            """
            INSERT OR IGNORE INTO news (headline_hash, timestamp, headline, source)
            VALUES (?, ?, ?, ?)
            """,
            ("hash_fake_1", int(time.time()), "Bitcoin bate recorde historico", "MockSource"),
        )
        conn.commit()
    finally:
        conn.close()


def run_cycles(cycles: int):
    from main import run_trading_cycle

    print(f"Rodando {cycles} ciclos em SQLite temporario: {database.get_db_path()}\n")
    for i in range(cycles):
        print(f"\n--- CICLO {i + 1}/{cycles} ---")
        try:
            run_trading_cycle()
        except Exception as e:
            print(f"CRASH FATAL NO CICLO {i + 1}: {type(e).__name__}: {e}")
            sys.exit(1)

    print(f"\n>>> SUCESSO: o sistema rodou {cycles} ciclos sem tocar no banco real. <<<")


def parse_args():
    parser = argparse.ArgumentParser(description="Run orchestration smoke cycles against a temporary SQLite DB.")
    parser.add_argument("--cycles", type=int, default=20, help="Number of smoke cycles. Default: 20")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_smoke_"))

    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        database.init_db()
        popular_banco_fake()
        run_cycles(args.cycles)
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)
