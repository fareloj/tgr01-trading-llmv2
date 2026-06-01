import sqlite3
from pathlib import Path

from backend.tests import dashboard_state


def create_minimal_dashboard_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE system_health (worker_name TEXT PRIMARY KEY, last_heartbeat INTEGER NOT NULL);
            CREATE TABLE klines (
                asset TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                close REAL NOT NULL
            );
            CREATE TABLE news (timestamp INTEGER NOT NULL, source TEXT NOT NULL, headline TEXT NOT NULL);
            CREATE TABLE virtual_portfolio (currency TEXT PRIMARY KEY, amount REAL NOT NULL);
            CREATE TABLE trade_logs (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                llm_action TEXT,
                llm_reasoning TEXT,
                action TEXT,
                llm_conviction REAL,
                system_reliability REAL,
                final_confidence REAL,
                executed_size REAL,
                execution_price REAL,
                reasoning TEXT,
                payload_snapshot_json TEXT
            );
            INSERT INTO virtual_portfolio VALUES ('BRL', 10000), ('BTC', 0);
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_dashboard_state_works_before_optional_rag_tables_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    create_minimal_dashboard_db(db_path)
    monkeypatch.setattr(dashboard_state, "REPORTS_DIR", tmp_path / "missing-reports")
    monkeypatch.setattr(
        dashboard_state,
        "check_clock_skew",
        lambda timeout: {"status": "OK", "skew_seconds": 0, "max_skew_seconds": 300},
    )

    state = dashboard_state.fetch_dashboard_state(db_path)

    assert state["rag"] == {"documents": 0, "chunks": 0, "retrievals": 0}
    assert state["reports"] == []
    assert state["portfolio"]["equity_brl"] == 10000


def test_dashboard_state_counts_optional_rag_tables_and_reports(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    create_minimal_dashboard_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE rag_documents (id INTEGER PRIMARY KEY);
            CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY);
            CREATE TABLE rag_retrieval_logs (id INTEGER PRIMARY KEY);
            INSERT INTO rag_documents VALUES (1), (2);
            INSERT INTO rag_chunks VALUES (1), (2), (3);
            INSERT INTO rag_retrieval_logs VALUES (1);
            """
        )
        conn.commit()
    finally:
        conn.close()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dashboard_state, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(
        dashboard_state,
        "check_clock_skew",
        lambda timeout: {"status": "OK", "skew_seconds": 0, "max_skew_seconds": 300},
    )

    state = dashboard_state.fetch_dashboard_state(db_path)

    assert state["rag"] == {"documents": 2, "chunks": 3, "retrievals": 1}
    assert state["reports"][0]["name"] == "report.json"
