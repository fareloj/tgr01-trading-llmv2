import os
import json
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update, insert, and_, or_, desc, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from backend.core import database
from backend.core.db_models import (
    metadata, klines, news, trade_logs, virtual_portfolio,
    paper_position_state, paper_position_reconciliations, system_health,
    rag_documents, rag_chunks, rag_retrieval_logs, analysis_tool_calls,
    market_events
)

class EngineProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(database.engine, name)

engine = EngineProxy()

def _execute_query(query, connection=None):
    """Executes a query on the provided connection, or acquires one from the engine."""
    if connection is not None:
        return connection.execute(query)
    else:
        with engine.begin() as conn:
            return conn.execute(query)

def init_db_schema():
    """Initializes the database schema."""
    from backend.core.database import init_db
    init_db()

def upsert_kline(kline_dict: Dict[str, Any], connection=None):
    """Upserts a single kline row."""
    stmt = pg_insert(klines).values(**kline_dict)
    stmt = stmt.on_conflict_do_update(
        constraint='uq_klines_asset_timeframe_timestamp',
        set_={
            'open': stmt.excluded.open,
            'high': stmt.excluded.high,
            'low': stmt.excluded.low,
            'close': stmt.excluded.close,
            'volume': stmt.excluded.volume
        }
    )
    _execute_query(stmt, connection=connection)

def add_klines(klines_list: List[Dict[str, Any]], connection=None) -> int:
    """Upserts multiple klines in one transaction and returns the processed count."""
    if connection is not None:
        for kline in klines_list:
            upsert_kline(kline, connection=connection)
        return len(klines_list)

    with engine.begin() as conn:
        for kline in klines_list:
            upsert_kline(kline, connection=conn)
    return len(klines_list)

def get_klines(asset: str, timeframe: str, limit: int, as_of_timestamp: Optional[int] = None, connection=None) -> List[Dict[str, Any]]:
    """Retrieves recent klines ordered by timestamp descending."""
    stmt = select(
        klines.c.timestamp,
        klines.c.open,
        klines.c.high,
        klines.c.low,
        klines.c.close,
        klines.c.volume
    ).where(
        and_(
            klines.c.asset == asset,
            klines.c.timeframe == timeframe
        )
    )
    if as_of_timestamp is not None:
        stmt = stmt.where(klines.c.timestamp <= as_of_timestamp)
    stmt = stmt.order_by(desc(klines.c.timestamp)).limit(limit)

    res = _execute_query(stmt, connection=connection)
    return [dict(r._mapping) for r in res]

def add_news(news_dict: Dict[str, Any], connection=None) -> bool:
    """Inserts news, ignoring conflicts on unique headline_hash."""
    stmt = pg_insert(news).values(**news_dict)
    stmt = stmt.on_conflict_do_nothing(index_elements=['headline_hash'])
    res = _execute_query(stmt, connection=connection)
    return bool(res.rowcount and res.rowcount > 0)


def get_recent_news(limit: int, start_timestamp: Optional[int] = None, end_timestamp: Optional[int] = None, connection=None) -> List[Dict[str, Any]]:
    """Retrieves latest news items for payload building or processing."""
    stmt = select(
        news.c.timestamp,
        news.c.headline,
        news.c.headline_hash,
        news.c.source,
        news.c.is_processed,
        news.c.processed_at
    )
    filters = []
    if start_timestamp is not None:
        filters.append(news.c.timestamp >= start_timestamp)
    if end_timestamp is not None:
        filters.append(news.c.timestamp <= end_timestamp)
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.order_by(desc(news.c.timestamp)).limit(limit)

    res = _execute_query(stmt, connection=connection)
    return [dict(r._mapping) for r in res]

def mark_news_as_processed(headline_hash: str, processed_at: int, connection=None):
    """Updates processed status for a news item."""
    stmt = update(news).where(news.c.headline_hash == headline_hash).values(
        is_processed=True,
        processed_at=processed_at
    )
    _execute_query(stmt, connection=connection)

def get_virtual_portfolio(connection=None, *, for_update: bool = False) -> Dict[str, float]:
    """Fetches currency amounts from virtual_portfolio."""
    stmt = select(virtual_portfolio.c.currency, virtual_portfolio.c.amount)
    if for_update:
        stmt = stmt.with_for_update()
    res = _execute_query(stmt, connection=connection)
    return {r[0]: r[1] for r in res}

def update_virtual_portfolio(currency: str, amount: float, connection=None):
    """Sets a currency amount directly."""
    stmt = update(virtual_portfolio).where(virtual_portfolio.c.currency == currency).values(amount=amount)
    _execute_query(stmt, connection=connection)

def update_virtual_portfolio_delta(currency: str, delta: float, connection=None):
    """Applies a delta to a currency amount."""
    stmt = update(virtual_portfolio).where(virtual_portfolio.c.currency == currency).values(
        amount=virtual_portfolio.c.amount + delta
    )
    _execute_query(stmt, connection=connection)

def add_trade_log(trade_log_dict: Dict[str, Any], *, connection) -> int:
    """Insert a trade log inside the caller-owned transaction."""
    stmt = insert(trade_logs).values(**trade_log_dict)
    res = _execute_query(stmt, connection=connection)
    if res.inserted_primary_key:
        return res.inserted_primary_key[0]
    return 0


def add_trade_log_autocommit(trade_log_dict: Dict[str, Any]) -> int:
    """Insert an operational audit in its own explicit transaction."""
    stmt = insert(trade_logs).values(**trade_log_dict)
    res = _execute_query(stmt)
    if res.inserted_primary_key:
        return res.inserted_primary_key[0]
    return 0

def get_trade_logs(action: Optional[str] = None, limit: Optional[int] = None, since_timestamp: Optional[int] = None, connection=None) -> List[Dict[str, Any]]:
    """Gets trade logs sorted chronologically."""
    stmt = select(trade_logs)
    filters = []
    if action is not None:
        filters.append(trade_logs.c.action == action)
    if since_timestamp is not None:
        filters.append(trade_logs.c.timestamp >= since_timestamp)
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.order_by(trade_logs.c.timestamp.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    res = _execute_query(stmt, connection=connection)
    return [dict(r._mapping) for r in res]

def get_trade_log_by_id(log_id: int, connection=None) -> Optional[Dict[str, Any]]:
    """Retrieves a single log by its primary key."""
    stmt = select(trade_logs).where(trade_logs.c.id == log_id)
    res = _execute_query(stmt, connection=connection).first()
    return dict(res._mapping) if res else None

def get_trade_log_ids(since_id: Optional[int] = None, limit: int = 100, connection=None) -> List[int]:
    """Retrieves IDs of logs having a payload snapshot, ordered by ID desc."""
    stmt = select(trade_logs.c.id).where(trade_logs.c.payload_snapshot_json.isnot(None))
    if since_id is not None:
        stmt = stmt.where(trade_logs.c.id >= since_id)
    stmt = stmt.order_by(desc(trade_logs.c.id)).limit(limit)
    res = _execute_query(stmt, connection=connection)
    return [r[0] for r in res]

def get_last_action_timestamp(action: str, since_timestamp: int, connection=None) -> Optional[int]:
    """Returns the timestamp of the latest matching action in cooldown window."""
    stmt = select(trade_logs.c.timestamp).where(
        and_(
            trade_logs.c.action == action,
            trade_logs.c.timestamp >= since_timestamp
        )
    ).order_by(desc(trade_logs.c.timestamp)).limit(1)
    res = _execute_query(stmt, connection=connection).first()
    return res[0] if res else None

def get_next_trade_log_id(connection=None) -> int:
    """Returns max(id) + 1 to serve as next experiment ID."""
    stmt = select(func.coalesce(func.max(trade_logs.c.id), 0) + 1)
    res = _execute_query(stmt, connection=connection).scalar()
    return res

def get_paper_position_state(asset: str, connection=None, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    """Fetches position details for the asset."""
    stmt = select(paper_position_state).where(paper_position_state.c.asset == asset)
    if for_update:
        stmt = stmt.with_for_update()
    res = _execute_query(stmt, connection=connection).first()
    return dict(res._mapping) if res else None

def update_paper_position_state(asset: str, quantity: float, avg_cost_brl: float, realized_pnl_brl: float, updated_at: int, connection=None):
    """Inserts or overrides the position state for the asset."""
    stmt = pg_insert(paper_position_state).values(
        asset=asset,
        quantity=quantity,
        avg_cost_brl=avg_cost_brl,
        realized_pnl_brl=realized_pnl_brl,
        updated_at=updated_at
    )
    stmt = stmt.on_conflict_do_update(
        constraint='paper_position_state_pkey',
        set_={
            'quantity': stmt.excluded.quantity,
            'avg_cost_brl': stmt.excluded.avg_cost_brl,
            'realized_pnl_brl': stmt.excluded.realized_pnl_brl,
            'updated_at': stmt.excluded.updated_at
        }
    )
    _execute_query(stmt, connection=connection)


def add_paper_position_reconciliation(data: Dict[str, Any], *, connection) -> int:
    stmt = insert(paper_position_reconciliations).values(**data)
    result = _execute_query(stmt, connection=connection)
    return int(result.inserted_primary_key[0]) if result.inserted_primary_key else 0


def get_latest_paper_position_reconciliation(asset: str, connection=None) -> Optional[Dict[str, Any]]:
    stmt = (
        select(paper_position_reconciliations)
        .where(paper_position_reconciliations.c.asset == asset)
        .order_by(paper_position_reconciliations.c.timestamp.desc(), paper_position_reconciliations.c.id.desc())
        .limit(1)
    )
    result = _execute_query(stmt, connection=connection).first()
    return dict(result._mapping) if result else None

def update_system_health(worker_name: str, last_heartbeat: int, connection=None):
    """Upserts the heartbeat timestamp for a worker."""
    stmt = pg_insert(system_health).values(
        worker_name=worker_name,
        last_heartbeat=last_heartbeat
    )
    stmt = stmt.on_conflict_do_update(
        constraint='system_health_pkey',
        set_={'last_heartbeat': stmt.excluded.last_heartbeat}
    )
    _execute_query(stmt, connection=connection)

def get_system_health(connection=None) -> List[Dict[str, Any]]:
    """Lists all worker heartbeats."""
    stmt = select(system_health).order_by(system_health.c.worker_name)
    res = _execute_query(stmt, connection=connection)
    return [dict(r._mapping) for r in res]

def clear_all_tables(connection=None):
    """Truncates or deletes all database tables."""
    tables_to_clear = [
        'klines', 'news', 'trade_logs', 'virtual_portfolio',
        'paper_position_state', 'paper_position_reconciliations', 'system_health',
        'rag_documents', 'rag_chunks', 'rag_retrieval_logs',
        'analysis_tool_calls', 'market_events'
    ]

    if connection is not None:
        connection.execute(text(f"TRUNCATE TABLE {', '.join(tables_to_clear)} RESTART IDENTITY CASCADE;"))
    else:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {', '.join(tables_to_clear)} RESTART IDENTITY CASCADE;"))

# RAG specific repository methods
def get_document_id_by_hash(content_hash: str, connection=None) -> Optional[int]:
    """Resolves a document ID from its content hash."""
    stmt = select(rag_documents.c.id).where(rag_documents.c.content_hash == content_hash)
    res = _execute_query(stmt, connection=connection).scalar()
    return res

def insert_document(source_type: str, source: str, title: str, content_hash: str, created_at: int, published_at: Optional[int], metadata_json: str, connection=None) -> int:
    """Stores a RAG document and returns its ID."""
    stmt = insert(rag_documents).values(
        source_type=source_type,
        source=source,
        title=title,
        content_hash=content_hash,
        created_at=created_at,
        published_at=published_at,
        metadata_json=metadata_json
    )
    res = _execute_query(stmt, connection=connection)
    if res.inserted_primary_key:
        return res.inserted_primary_key[0]
    return 0

def insert_chunk(document_id: int, chunk_index: int, text: str, token_estimate: int, metadata_json: str, connection=None) -> int:
    """Stores a RAG chunk and returns its ID."""
    stmt = insert(rag_chunks).values(
        document_id=document_id,
        chunk_index=chunk_index,
        text=text,
        token_estimate=token_estimate,
        metadata_json=metadata_json
    )
    res = _execute_query(stmt, connection=connection)
    if res.inserted_primary_key:
        return res.inserted_primary_key[0]
    return 0

def search_chunks(source_types: Optional[List[str]] = None, max_age_seconds: Optional[int] = None, now: Optional[int] = None, connection=None) -> List[Dict[str, Any]]:
    """Performs a joint search on RAG chunks and documents."""
    stmt = select(
        rag_chunks.c.id.label("chunk_id"),
        rag_chunks.c.document_id,
        rag_chunks.c.chunk_index,
        rag_chunks.c.text,
        rag_chunks.c.metadata_json.label("chunk_metadata_json"),
        rag_documents.c.title,
        rag_documents.c.source_type,
        rag_documents.c.source,
        rag_documents.c.published_at,
        rag_documents.c.metadata_json.label("doc_metadata_json"),
        rag_documents.c.created_at
    ).select_from(
        rag_chunks.join(rag_documents, rag_documents.c.id == rag_chunks.c.document_id)
    )

    filters = []
    if source_types is not None:
        filters.append(rag_documents.c.source_type.in_(source_types))
    if max_age_seconds is not None and now is not None:
        cutoff = now - max_age_seconds
        filters.append(
            or_(
                and_(rag_documents.c.published_at.isnot(None), rag_documents.c.published_at >= cutoff),
                and_(rag_documents.c.published_at.is_(None), rag_documents.c.created_at >= cutoff)
            )
        )
    if filters:
        stmt = stmt.where(and_(*filters))

    stmt = stmt.order_by(
        desc(func.coalesce(rag_documents.c.published_at, rag_documents.c.created_at)),
        desc(rag_chunks.c.id)
    ).limit(250)

    res = _execute_query(stmt, connection=connection)
    return [dict(r._mapping) for r in res]

def insert_retrieval_log(timestamp: int, purpose: str, query: str, filters_json: str, selected_chunk_ids_json: str, connection=None):
    """Inserts a record of a RAG query execution."""
    stmt = insert(rag_retrieval_logs).values(
        timestamp=timestamp,
        purpose=purpose,
        query=query,
        filters_json=filters_json,
        selected_chunk_ids_json=selected_chunk_ids_json
    )
    _execute_query(stmt, connection=connection)


def add_analysis_tool_call(data: Dict[str, Any], connection=None) -> int:
    """Persist a bounded tool request/result pair without exposing model secrets."""
    stmt = insert(analysis_tool_calls).values(**data)
    result = _execute_query(stmt, connection=connection)
    return int(result.inserted_primary_key[0]) if result.inserted_primary_key else 0


def get_analysis_tool_calls(limit: int = 100, connection=None) -> List[Dict[str, Any]]:
    stmt = select(analysis_tool_calls).order_by(desc(analysis_tool_calls.c.id)).limit(limit)
    result = _execute_query(stmt, connection=connection)
    return [dict(row._mapping) for row in result]


def add_market_event(data: Dict[str, Any], connection=None) -> bool:
    """Insert an objective event once; duplicate detections are intentionally ignored."""
    stmt = pg_insert(market_events).values(**data)
    stmt = stmt.on_conflict_do_nothing(index_elements=['dedupe_key'])
    stmt = stmt.returning(market_events.c.id)
    result = _execute_query(stmt, connection=connection)
    return result.scalar_one_or_none() is not None


def get_market_events(asset: Optional[str] = None, limit: int = 100, connection=None) -> List[Dict[str, Any]]:
    stmt = select(market_events)
    if asset is not None:
        stmt = stmt.where(market_events.c.asset == asset)
    stmt = stmt.order_by(desc(market_events.c.event_timestamp)).limit(limit)
    result = _execute_query(stmt, connection=connection)
    return [dict(row._mapping) for row in result]
