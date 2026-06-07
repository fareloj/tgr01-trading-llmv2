import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core import database
from rag import decision_memory
from rag import rag_store


def test_rag_store_retrieves_relevant_chunks(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="tgr01_rag_test_") as tmp:
        test_db = Path(tmp) / "rag_test.db"
        monkeypatch.setattr(database, "DB_PATH", test_db)
        monkeypatch.setattr(rag_store, "get_connection", database.get_connection)

        rag_store.init_rag_tables()
        doc_id = rag_store.upsert_document(
            source_type="study_note",
            source="unit-test",
            title="RSI oversold is not enough",
            text=(
                "RSI oversold sozinho nao autoriza BUY. "
                "Se MACD estiver BEARISH_EXPANDING, prefira HOLD."
            ),
            published_at=1000,
        )

        chunks = rag_store.search_chunks(
            "RSI oversold MACD bearish",
            source_types=["study_note"],
            limit=3,
            now=1200,
            log_retrieval=False,
        )

        assert doc_id > 0
        assert len(chunks) == 1
        assert chunks[0].source_type == "study_note"
        assert "MACD" in chunks[0].text


def test_rag_context_block_is_push_only_language():
    block = rag_store.build_context_block([])

    assert "Nenhum contexto recuperado" in block


def test_rag_upsert_is_idempotent(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="tgr01_rag_test_") as tmp:
        test_db = Path(tmp) / "rag_test.db"
        monkeypatch.setattr(database, "DB_PATH", test_db)
        monkeypatch.setattr(rag_store, "get_connection", database.get_connection)

        first_id = rag_store.upsert_document(
            source_type="risk_rule",
            source="unit-test",
            title="Cooldown",
            text="BUY repetido precisa respeitar cooldown antes de nova entrada.",
            published_at=1000,
        )
        second_id = rag_store.upsert_document(
            source_type="risk_rule",
            source="unit-test",
            title="Cooldown",
            text="BUY repetido precisa respeitar cooldown antes de nova entrada.",
            published_at=1000,
        )

        assert second_id == first_id


def test_decision_memory_ingests_trade_log_case(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="tgr01_rag_test_") as tmp:
        test_db = Path(tmp) / "rag_test.db"
        monkeypatch.setattr(database, "DB_PATH", test_db)
        monkeypatch.setattr(rag_store, "get_connection", database.get_connection)
        monkeypatch.setattr(decision_memory, "get_connection", database.get_connection)

        database.init_db()
        payload = {
            "schema_version": 1,
            "technical": {
                "current_price": 100000,
                "rsi_value": 29.5,
                "rsi_status": "OVERSOLD",
                "macd_histogram": -10,
                "macd_status": "BEARISH_EXPANDING",
                "volatility_atr": 500,
            },
            "data_health": {
                "kline_age_seconds": 60,
                "news_age_seconds": 600,
                "is_market_data_stale": False,
                "is_news_stale": False,
            },
            "news_risk": {
                "risk_level": "NORMAL",
                "has_negative_red_flag": False,
                "matched_terms": [],
            },
            "portfolio": {"current_exposure_percentage": 10, "is_in_drawdown": False},
            "recent_news": [{"headline": "Bitcoin volatility rises", "source": "pytest"}],
        }
        conn = database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trade_logs (
                    timestamp, llm_action, llm_reasoning, llm_decision_brief,
                    action, llm_conviction, system_reliability, final_confidence,
                    executed_size, execution_price, reasoning, payload_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1200,
                    "HOLD",
                    "RSI oversold, MACD bearish",
                    "Acao HOLD.\nBase tecnica: RSI oversold e MACD bearish.\nContexto: dados frescos.",
                    "HOLD",
                    80,
                    1.0,
                    0.8,
                    0.0,
                    100000,
                    "LLM sugeriu HOLD.",
                    __import__("json").dumps(payload),
                ),
            )
            conn.commit()
            log_id = int(cursor.lastrowid)
        finally:
            conn.close()

        doc_id = decision_memory.upsert_trade_log_case(log_id)
        chunks = decision_memory.retrieve_context_for_trade_log(log_id, limit=3)

        assert doc_id > 0
        assert chunks
        assert chunks[0].source_type == "decision_case"
        assert "BEARISH_EXPANDING" in chunks[0].text
