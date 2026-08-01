import math
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from backend.core import database, repository
from backend.core.db_models import paper_position_reconciliations, trade_logs
from backend.execution.legacy_reconciliation import (
    assert_reconstruction_matches_portfolio,
    reconstruct_legacy_orders,
)
from backend.ops.reconcile_paper_position import reconcile_from_legacy_logs


LEGACY_PRICES = [395966.0, 403589.0, 401304.0, 360752.0]
EXPECTED_BRL = 8007.158723465673
EXPECTED_BTC = 0.005098234477605994
EXPECTED_AVG_COST = 390888.50959834974


def _rows():
    return [
        {
            "id": index,
            "action": "BUY",
            "executed_size": 5.0,
            "execution_price": price,
            "fee_brl": None,
            "brl_delta": None,
            "btc_delta": None,
        }
        for index, price in enumerate(LEGACY_PRICES, start=1)
    ]


def _seed_legacy_database(connection):
    repository.update_virtual_portfolio("BRL", EXPECTED_BRL, connection=connection)
    repository.update_virtual_portfolio("BTC", EXPECTED_BTC, connection=connection)
    for row in _rows():
        connection.execute(
            trade_logs.insert().values(
                id=row["id"],
                timestamp=row["id"],
                llm_action="BUY",
                action="BUY",
                llm_conviction=80.0,
                executed_size=row["executed_size"],
                execution_price=row["execution_price"],
                reasoning="legacy approved",
            )
        )


def test_known_legacy_orders_reconstruct_exact_portfolio_and_cost_basis():
    reconstruction = reconstruct_legacy_orders(_rows())

    assert reconstruction.final_brl == EXPECTED_BRL
    assert reconstruction.final_btc == EXPECTED_BTC
    assert reconstruction.avg_cost_brl == EXPECTED_AVG_COST
    assert reconstruction.realized_pnl_brl == 0.0
    assert reconstruction.source_log_ids == [1, 2, 3, 4]
    assert_reconstruction_matches_portfolio(
        reconstruction,
        {"BRL": EXPECTED_BRL, "BTC": EXPECTED_BTC},
    )


def test_one_cent_balance_tamper_rejects_reconstruction():
    reconstruction = reconstruct_legacy_orders(_rows())

    with pytest.raises(RuntimeError, match="nao fecha BRL"):
        assert_reconstruction_matches_portfolio(
            reconstruction,
            {"BRL": EXPECTED_BRL + 0.01, "BTC": EXPECTED_BTC},
        )


def test_mixed_modern_audit_is_rejected_instead_of_guessed():
    rows = _rows()
    rows[2]["fee_brl"] = 1.5

    with pytest.raises(ValueError, match="mistura auditoria moderna"):
        reconstruct_legacy_orders(rows)


def test_reconciliation_is_audited_and_does_not_change_portfolio():
    with database.engine.begin() as connection:
        _seed_legacy_database(connection)
        before = repository.get_virtual_portfolio(connection=connection)

        result = reconcile_from_legacy_logs(connection)

        after = repository.get_virtual_portfolio(connection=connection)
        position = repository.get_paper_position_state("BTC/BRL", connection=connection)
        audit = repository.get_latest_paper_position_reconciliation("BTC/BRL", connection=connection)

    assert after == before
    assert result["method"] == "legacy_trade_log_replay_v1"
    assert position is not None
    assert math.isclose(position["quantity"], EXPECTED_BTC, rel_tol=0.0, abs_tol=1e-15)
    assert position["avg_cost_brl"] == EXPECTED_AVG_COST
    assert audit is not None
    assert audit["method"] == "legacy_trade_log_replay_v1"
    assert audit["source_log_ids_json"] == "[1, 2, 3, 4]"
    assert audit["observed_brl"] == EXPECTED_BRL
    assert audit["observed_btc"] == EXPECTED_BTC


def test_failed_reconciliation_rolls_back_position_and_audit():
    with pytest.raises(RuntimeError, match="nao fecha BRL"):
        with database.engine.begin() as connection:
            _seed_legacy_database(connection)
            repository.update_virtual_portfolio("BRL", EXPECTED_BRL + 0.01, connection=connection)
            reconcile_from_legacy_logs(connection)

    with database.engine.connect() as connection:
        assert repository.get_paper_position_state("BTC/BRL", connection=connection) is None
        count = int(connection.scalar(select(func.count()).select_from(paper_position_reconciliations)) or 0)
    assert count == 0


def test_concurrent_reconciliation_creates_exactly_one_position_and_audit():
    with database.engine.begin() as connection:
        _seed_legacy_database(connection)

    def attempt():
        try:
            with database.engine.begin() as connection:
                result = reconcile_from_legacy_logs(connection)
            return result["reconciliation_id"]
        except RuntimeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(2)))

    successes = [value for value in outcomes if isinstance(value, int)]
    failures = [value for value in outcomes if isinstance(value, str)]
    assert len(successes) == 1
    assert failures == ["paper_position_state ja existe; replay legado nao pode sobrescreve-lo."]

    with database.engine.connect() as connection:
        position = repository.get_paper_position_state("BTC/BRL", connection=connection)
        count = int(connection.scalar(select(func.count()).select_from(paper_position_reconciliations)) or 0)
    assert position["quantity"] == EXPECTED_BTC
    assert count == 1
