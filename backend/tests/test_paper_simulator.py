import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core import database
from execution.paper_simulator import PaperExecutionConfig, execute_paper_order


def _payload(price: float = 100000.0, atr: float = 500.0) -> dict:
    return {
        "technical_context": {
            "current_price": price,
            "volatility_atr": atr,
        }
    }


def _with_temp_db():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_paper_sim_"))
    database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
    database.init_db()
    return original_db_path, temp_dir


def _restore_db(original_db_path: Path, temp_dir: Path):
    database.DB_PATH = original_db_path
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_buy_applies_fee_slippage_and_updates_average_cost():
    original_db_path, temp_dir = _with_temp_db()
    try:
        conn = database.get_connection()
        try:
            cursor = conn.cursor()
            result = execute_paper_order(
                cursor=cursor,
                action="BUY",
                executed_size_pct=5.0,
                current_price=100000.0,
                payload=_payload(),
                config=PaperExecutionConfig(fee_rate=0.003, min_slippage_rate=0.001, max_slippage_rate=0.001),
            )
            conn.commit()

            brl = cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BRL'").fetchone()["amount"]
            btc = cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BTC'").fetchone()["amount"]
            position = cursor.execute("SELECT quantity, avg_cost_brl FROM paper_position_state WHERE asset='BTC/BRL'").fetchone()
        finally:
            conn.close()

        assert round(result["gross_notional_brl"], 2) == 500.00
        assert round(result["fee_brl"], 2) == 1.50
        assert round(result["effective_price"], 2) == 100100.0
        assert round(brl, 2) == 9500.00
        assert btc > 0
        assert round(position["quantity"], 12) == round(btc, 12)
        assert position["avg_cost_brl"] > result["effective_price"]
        assert result["equity_after_brl"] < result["equity_before_brl"]
    finally:
        _restore_db(original_db_path, temp_dir)


def test_sell_uses_average_cost_and_records_realized_pnl():
    original_db_path, temp_dir = _with_temp_db()
    try:
        conn = database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE virtual_portfolio SET amount = 0 WHERE currency='BRL'")
            cursor.execute("UPDATE virtual_portfolio SET amount = 0.1 WHERE currency='BTC'")
            cursor.execute(
                """
                INSERT INTO paper_position_state (asset, quantity, avg_cost_brl, realized_pnl_brl, updated_at)
                VALUES ('BTC/BRL', 0.1, 90000.0, 0.0, 1)
                """
            )
            result = execute_paper_order(
                cursor=cursor,
                action="SELL",
                executed_size_pct=50.0,
                current_price=100000.0,
                payload=_payload(),
                config=PaperExecutionConfig(fee_rate=0.003, min_slippage_rate=0.001, max_slippage_rate=0.001),
            )
            conn.commit()

            brl = cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BRL'").fetchone()["amount"]
            btc = cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BTC'").fetchone()["amount"]
            position = cursor.execute("SELECT quantity, avg_cost_brl, realized_pnl_brl FROM paper_position_state WHERE asset='BTC/BRL'").fetchone()
        finally:
            conn.close()

        assert round(result["effective_price"], 2) == 99900.0
        assert result["btc_delta"] < 0
        assert brl > 0
        assert btc < 0.1
        assert result["realized_pnl_brl"] > 0
        assert position["realized_pnl_brl"] == result["realized_pnl_brl"]
        assert position["avg_cost_brl"] == 90000.0
    finally:
        _restore_db(original_db_path, temp_dir)


def test_init_db_migrates_execution_audit_columns():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_paper_migration_"))
    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        conn = sqlite3.connect(database.DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE trade_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    action TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        database.init_db()
        conn = database.get_connection()
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(trade_logs)").fetchall()}
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()

        assert "paper_position_state" in tables
        assert "effective_price" in columns
        assert "fee_brl" in columns
        assert "realized_pnl_brl" in columns
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)
