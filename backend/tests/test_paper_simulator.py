import sys
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.core import database
from backend.execution.paper_simulator import PaperExecutionConfig, execute_paper_order


def _payload(price: float = 100000.0, atr: float = 500.0) -> dict:
    return {
        "technical_context": {
            "current_price": price,
            "volatility_atr": atr,
        }
    }


def test_buy_applies_fee_slippage_and_updates_average_cost():
    from backend.core import repository
    with database.get_connection() as conn:
        result = execute_paper_order(
            connection=conn,
            action="BUY",
            executed_size_pct=5.0,
            current_price=100000.0,
            payload=_payload(),
            config=PaperExecutionConfig(fee_rate=0.003, min_slippage_rate=0.001, max_slippage_rate=0.001),
        )

        portfolio = repository.get_virtual_portfolio(connection=conn)
        brl = portfolio.get("BRL", 0.0)
        btc = portfolio.get("BTC", 0.0)
        position = repository.get_paper_position_state("BTC/BRL", connection=conn)

    assert round(result["gross_notional_brl"], 2) == 500.00
    assert round(result["fee_brl"], 2) == 1.50
    assert round(result["effective_price"], 2) == 100100.0
    assert round(brl, 2) == 9500.00
    assert btc > 0
    assert round(position["quantity"], 12) == round(btc, 12)
    assert position["avg_cost_brl"] > result["effective_price"]
    assert result["equity_after_brl"] < result["equity_before_brl"]


def test_sell_uses_average_cost_and_records_realized_pnl():
    from backend.core import repository
    with database.get_connection() as conn:
        repository.update_virtual_portfolio("BRL", 0.0, connection=conn)
        repository.update_virtual_portfolio("BTC", 0.1, connection=conn)
        repository.update_paper_position_state(
            asset="BTC/BRL",
            quantity=0.1,
            avg_cost_brl=90000.0,
            realized_pnl_brl=0.0,
            updated_at=1,
            connection=conn
        )

        result = execute_paper_order(
            connection=conn,
            action="SELL",
            executed_size_pct=50.0,
            current_price=100000.0,
            payload=_payload(),
            config=PaperExecutionConfig(fee_rate=0.003, min_slippage_rate=0.001, max_slippage_rate=0.001),
        )

        portfolio = repository.get_virtual_portfolio(connection=conn)
        brl = portfolio.get("BRL", 0.0)
        btc = portfolio.get("BTC", 0.0)
        position = repository.get_paper_position_state("BTC/BRL", connection=conn)

    assert round(result["effective_price"], 2) == 99900.0
    assert result["btc_delta"] < 0
    assert brl > 0
    assert btc < 0.1
    assert result["realized_pnl_brl"] > 0
    assert position["realized_pnl_brl"] == result["realized_pnl_brl"]
    assert position["avg_cost_brl"] == 90000.0


def test_init_db_migrates_execution_audit_columns():
    from sqlalchemy import inspect
    database.init_db()
    inspector = inspect(database.engine)
    tables = inspector.get_table_names()
    columns = [col["name"] for col in inspector.get_columns("trade_logs")]

    assert "paper_position_state" in tables
    assert "paper_position_reconciliations" in tables
    assert "effective_price" in columns
    assert "fee_brl" in columns
    assert "realized_pnl_brl" in columns


def test_non_finite_execution_input_fails_before_mutation():
    from backend.core import repository

    with database.engine.begin() as conn:
        before = repository.get_virtual_portfolio(connection=conn)
        with pytest.raises(ValueError, match="finite"):
            execute_paper_order(
                connection=conn,
                action="BUY",
                executed_size_pct=5.0,
                current_price=math.nan,
                payload=_payload(),
            )
        after = repository.get_virtual_portfolio(connection=conn)

    assert after == before


def test_missing_portfolio_currency_fails_loudly():
    from sqlalchemy import delete
    from backend.core import repository
    from backend.core.db_models import virtual_portfolio

    with database.engine.begin() as conn:
        conn.execute(delete(virtual_portfolio).where(virtual_portfolio.c.currency == "BTC"))
        with pytest.raises(RuntimeError, match="moedas ausentes: BTC"):
            execute_paper_order(
                connection=conn,
                action="BUY",
                executed_size_pct=5.0,
                current_price=100000.0,
                payload=_payload(),
            )


def test_existing_btc_without_position_requires_explicit_reconciliation():
    from backend.core import repository

    with database.engine.begin() as conn:
        repository.update_virtual_portfolio("BRL", 9000.0, connection=conn)
        repository.update_virtual_portfolio("BTC", 0.01, connection=conn)
        before = repository.get_virtual_portfolio(connection=conn)

        with pytest.raises(RuntimeError, match="Estado legado nao reconciliado"):
            execute_paper_order(
                connection=conn,
                action="BUY",
                executed_size_pct=5.0,
                current_price=100000.0,
                payload=_payload(),
            )

        after = repository.get_virtual_portfolio(connection=conn)
        position = repository.get_paper_position_state("BTC/BRL", connection=conn)

    assert after == before
    assert position is None


def test_explicit_reconciliation_sets_cost_basis_without_changing_balances():
    from backend.core import repository
    from backend.ops.reconcile_paper_position import reconcile_position

    with database.engine.begin() as conn:
        repository.update_virtual_portfolio("BRL", 9000.0, connection=conn)
        repository.update_virtual_portfolio("BTC", 0.01, connection=conn)
        before = repository.get_virtual_portfolio(connection=conn)

        result = reconcile_position(conn, avg_cost_brl=95000.0)

        after = repository.get_virtual_portfolio(connection=conn)
        position = repository.get_paper_position_state("BTC/BRL", connection=conn)

    assert after == before
    assert result["quantity"] == 0.01
    assert position["quantity"] == 0.01
    assert position["avg_cost_brl"] == 95000.0


def test_concurrent_buys_lock_capital_and_keep_position_consistent():
    from backend.core import repository

    def buy_once(_index: int) -> None:
        with database.engine.begin() as conn:
            execute_paper_order(
                connection=conn,
                action="BUY",
                executed_size_pct=5.0,
                current_price=100000.0,
                payload=_payload(),
                config=PaperExecutionConfig(
                    fee_rate=0.003,
                    min_slippage_rate=0.001,
                    max_slippage_rate=0.001,
                ),
            )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(buy_once, range(12)))

    with database.engine.connect() as conn:
        portfolio = repository.get_virtual_portfolio(connection=conn)
        position = repository.get_paper_position_state("BTC/BRL", connection=conn)

    assert 0 <= portfolio["BRL"] < 10000.0
    assert portfolio["BTC"] > 0
    assert position is not None
    assert math.isclose(position["quantity"], portfolio["BTC"], rel_tol=1e-9, abs_tol=1e-12)
