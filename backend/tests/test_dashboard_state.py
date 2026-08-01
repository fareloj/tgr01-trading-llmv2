import json
import time

from backend.core import database
from backend.core.db_models import (
    klines,
    paper_position_reconciliations,
    paper_position_state,
    rag_chunks,
    rag_documents,
    rag_retrieval_logs,
)
from backend.tests import dashboard_state


def _stub_clock(monkeypatch):
    monkeypatch.setattr(
        dashboard_state,
        "check_clock_skew",
        lambda timeout: {"status": "OK", "skew_seconds": 0, "max_skew_seconds": 300},
    )
    monkeypatch.setattr(
        dashboard_state,
        "get_external_rag_health",
        lambda: {"status": "ready", "reachable": True, "dense_indexed": 10, "lexical_indexed": 10},
    )


def test_dashboard_state_uses_postgresql_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_state, "REPORTS_DIR", tmp_path / "missing-reports")
    _stub_clock(monkeypatch)

    state = dashboard_state.fetch_dashboard_state()

    assert state["database"]["backend"] == "PostgreSQL"
    assert "***" in state["database"]["label"]
    assert state["rag"] == {"documents": 0, "chunks": 0, "retrievals": 0}
    assert state["external_rag"]["status"] == "ready"
    assert state["reports"] == []
    assert state["portfolio"]["equity_brl"] == 10000
    assert state["position"] is None


def test_dashboard_state_counts_rag_and_reports(tmp_path, monkeypatch):
    now = int(time.time())
    with database.engine.begin() as conn:
        document_id = conn.execute(
            rag_documents.insert().values(
                source_type="test",
                source="test.md",
                title="Test",
                content_hash="dashboard-test",
                created_at=now,
                metadata_json="{}",
            )
        ).inserted_primary_key[0]
        conn.execute(
            rag_chunks.insert().values(
                document_id=document_id,
                chunk_index=0,
                text="content",
                token_estimate=2,
                metadata_json="{}",
            )
        )
        conn.execute(
            rag_retrieval_logs.insert().values(
                timestamp=now,
                purpose="test",
                query="content",
                filters_json="{}",
                selected_chunk_ids_json=json.dumps([]),
            )
        )
        conn.execute(
            klines.insert().values(
                asset="BTC/BRL",
                timeframe="1m",
                timestamp=now,
                open=100,
                high=100,
                low=100,
                close=100,
                volume=1,
            )
        )
        conn.execute(
            paper_position_state.insert().values(
                asset="BTC/BRL",
                quantity=0.01,
                avg_cost_brl=90000,
                realized_pnl_brl=15,
                updated_at=now,
            )
        )
        conn.execute(
            paper_position_reconciliations.insert().values(
                asset="BTC/BRL",
                timestamp=now,
                method="legacy_trade_log_replay_v1",
                initial_brl=10000,
                initial_btc=0,
                reconstructed_brl=9000,
                reconstructed_btc=0.01,
                observed_brl=9000,
                observed_btc=0.01,
                avg_cost_brl=90000,
                realized_pnl_brl=15,
                source_log_ids_json="[7]",
                details_json="{}",
            )
        )

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dashboard_state, "REPORTS_DIR", reports_dir)
    _stub_clock(monkeypatch)

    state = dashboard_state.fetch_dashboard_state()

    assert state["rag"] == {"documents": 1, "chunks": 1, "retrievals": 1}
    assert state["reports"][0]["name"] == "report.json"
    assert state["position"]["avg_cost_brl"] == 90000
    assert state["position"]["reconciliation"] == {
        "id": 1,
        "timestamp": now,
        "method": "legacy_trade_log_replay_v1",
        "source_log_ids": [7],
    }
