import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core import database
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
