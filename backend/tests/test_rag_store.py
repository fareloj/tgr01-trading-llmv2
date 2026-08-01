import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.core import database
from backend.rag import decision_memory
from backend.rag import rag_store


def test_rag_store_retrieves_relevant_chunks():
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


def test_rag_upsert_is_idempotent():
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


def test_decision_memory_ingests_trade_log_case():
    from backend.core import repository
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

    log_data = {
        "timestamp": 1200,
        "llm_action": "HOLD",
        "llm_reasoning": "RSI oversold, MACD bearish",
        "llm_decision_brief": "Acao HOLD.\nBase tecnica: RSI oversold e MACD bearish.\nContexto: dados frescos.",
        "action": "HOLD",
        "llm_conviction": 80.0,
        "system_reliability": 1.0,
        "final_confidence": 0.8,
        "executed_size": 0.0,
        "execution_price": 100000.0,
        "reasoning": "LLM sugeriu HOLD.",
        "payload_snapshot_json": __import__("json").dumps(payload),
    }
    log_id = repository.add_trade_log_autocommit(log_data)

    doc_id = decision_memory.upsert_trade_log_case(log_id)
    chunks = decision_memory.retrieve_context_for_trade_log(log_id, limit=3)

    assert doc_id > 0
    assert chunks
    assert chunks[0].source_type == "decision_case"
    assert "BEARISH_EXPANDING" in chunks[0].text
