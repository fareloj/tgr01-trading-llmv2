import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.database import get_connection
from rag.rag_store import RagChunk, build_context_block, search_chunks, upsert_document


DEFAULT_DECISION_SOURCE_TYPES = [
    "risk_rule",
    "study_note",
    "architecture_note",
    "decision_case",
    "news_summary",
]


def build_decision_query_from_payload(payload: dict) -> str:
    """Build a deterministic retrieval query from a market payload."""
    technical = payload.get("technical_context", {})
    data_health = payload.get("data_health", {})
    news_risk = payload.get("news_risk", {})
    portfolio = payload.get("portfolio_context", {})

    rsi = technical.get("rsi", {})
    macd = technical.get("macd", {})
    terms = []
    terms.extend(
        [
            "BTC BRL decision review",
            f"RSI {rsi.get('status', 'UNKNOWN')} {rsi.get('value', '')}",
            f"MACD {macd.get('status', 'UNKNOWN')} {macd.get('histogram', '')}",
            f"ATR {technical.get('volatility_atr', '')}",
            f"market stale {data_health.get('is_market_data_stale')}",
            f"news stale {data_health.get('is_news_stale')}",
            f"news risk {news_risk.get('risk_level', 'UNKNOWN')}",
            f"exposure {portfolio.get('current_exposure_percentage', '')}",
        ]
    )
    terms.extend(str(term) for term in news_risk.get("matched_terms", []))
    for item in payload.get("news_context", [])[:3]:
        terms.append(str(item.get("headline", ""))[:160])
    return " | ".join(part for part in terms if part.strip())


def build_decision_query_from_snapshot(snapshot: dict) -> str:
    """Build a retrieval query from the compact trade_logs payload snapshot."""
    technical = snapshot.get("technical", {})
    data_health = snapshot.get("data_health", {})
    news_risk = snapshot.get("news_risk", {})
    portfolio = snapshot.get("portfolio", {})

    terms = [
        "BTC BRL decision review",
        f"RSI {technical.get('rsi_status', 'UNKNOWN')} {technical.get('rsi_value', '')}",
        f"MACD {technical.get('macd_status', 'UNKNOWN')} {technical.get('macd_histogram', '')}",
        f"ATR {technical.get('volatility_atr', '')}",
        f"market stale {data_health.get('is_market_data_stale')}",
        f"news stale {data_health.get('is_news_stale')}",
        f"news risk {news_risk.get('risk_level', 'UNKNOWN')}",
        f"exposure {portfolio.get('current_exposure_percentage', '')}",
    ]
    terms.extend(str(term) for term in news_risk.get("matched_terms", []))
    for item in snapshot.get("recent_news", [])[:3]:
        terms.append(str(item.get("headline", ""))[:160])
    return " | ".join(part for part in terms if part.strip())


def retrieve_decision_context(
    payload: dict,
    *,
    limit: int = 6,
    source_types: list[str] | None = None,
    purpose: str = "decision_context_review",
) -> list[RagChunk]:
    """Retrieve auxiliary memory for human review of a payload.

    This function is intentionally push-only: callers decide whether to show the
    context to a human or to a reviewer LLM. It does not approve or block orders.
    """
    query = build_decision_query_from_payload(payload)
    return search_chunks(
        query,
        source_types=source_types or DEFAULT_DECISION_SOURCE_TYPES,
        limit=limit,
        purpose=purpose,
    )


def build_decision_context_block(payload: dict, *, limit: int = 6) -> str:
    chunks = retrieve_decision_context(payload, limit=limit)
    return build_context_block(chunks, title="DECISION MEMORY CONTEXT")


def load_trade_log_snapshot(log_id: int) -> tuple[dict, dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT id, timestamp, llm_action, llm_reasoning, llm_decision_brief,
                   action, reasoning, payload_snapshot_json
            FROM trade_logs
            WHERE id = ?
            """,
            (log_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError(f"trade_log id {log_id} nao encontrado")
    if not row["payload_snapshot_json"]:
        raise ValueError(f"trade_log id {log_id} nao possui payload_snapshot_json")

    snapshot = json.loads(row["payload_snapshot_json"])
    metadata = {key: row[key] for key in row.keys() if key != "payload_snapshot_json"}
    return snapshot, metadata


def retrieve_context_for_trade_log(log_id: int, *, limit: int = 6) -> list[RagChunk]:
    snapshot, metadata = load_trade_log_snapshot(log_id)
    query = build_decision_query_from_snapshot(snapshot)
    return search_chunks(
        query,
        source_types=DEFAULT_DECISION_SOURCE_TYPES,
        limit=limit,
        purpose=f"trade_log_context:{metadata['id']}",
    )


def build_trade_log_context_block(log_id: int, *, limit: int = 6) -> str:
    chunks = retrieve_context_for_trade_log(log_id, limit=limit)
    return build_context_block(chunks, title=f"DECISION MEMORY FOR TRADE_LOG {log_id}")


def upsert_trade_log_case(log_id: int) -> int:
    """Store an auditable trade log as a retrievable decision case."""
    snapshot, metadata = load_trade_log_snapshot(log_id)
    technical = snapshot.get("technical", {})
    data_health = snapshot.get("data_health", {})
    news_risk = snapshot.get("news_risk", {})
    portfolio = snapshot.get("portfolio", {})

    title = (
        f"trade_log {log_id}: LLM {metadata.get('llm_action')} "
        f"final {metadata.get('action')}"
    )
    text = "\n".join(
        [
            title,
            f"timestamp: {metadata.get('timestamp')}",
            f"llm_reasoning: {metadata.get('llm_reasoning')}",
            f"llm_decision_brief: {metadata.get('llm_decision_brief') or ''}",
            f"risk_reasoning: {metadata.get('reasoning')}",
            (
                "technical: "
                f"price={technical.get('current_price')} "
                f"RSI={technical.get('rsi_value')} {technical.get('rsi_status')} "
                f"MACD={technical.get('macd_histogram')} {technical.get('macd_status')} "
                f"ATR={technical.get('volatility_atr')}"
            ),
            (
                "data_health: "
                f"kline_age={data_health.get('kline_age_seconds')} "
                f"news_age={data_health.get('news_age_seconds')} "
                f"market_stale={data_health.get('is_market_data_stale')} "
                f"news_stale={data_health.get('is_news_stale')}"
            ),
            (
                "news_risk: "
                f"level={news_risk.get('risk_level')} "
                f"red_flag={news_risk.get('has_negative_red_flag')} "
                f"terms={news_risk.get('matched_terms', [])}"
            ),
            (
                "portfolio: "
                f"exposure={portfolio.get('current_exposure_percentage')} "
                f"drawdown={portfolio.get('is_in_drawdown')}"
            ),
        ]
    )
    return upsert_document(
        source_type="decision_case",
        source=f"trade_logs:{log_id}",
        title=title,
        text=text,
        published_at=metadata.get("timestamp"),
        metadata={
            "trade_log_id": log_id,
            "llm_action": metadata.get("llm_action"),
            "final_action": metadata.get("action"),
        },
    )


def upsert_recent_trade_log_cases(*, since_id: int | None = None, limit: int = 100) -> list[int]:
    where = "WHERE payload_snapshot_json IS NOT NULL"
    params: list[int] = []
    if since_id is not None:
        where += " AND id >= ?"
        params.append(since_id)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            f"""
            SELECT id
            FROM trade_logs
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()

    touched = []
    for row in rows:
        touched.append(upsert_trade_log_case(int(row["id"])))
    return touched
