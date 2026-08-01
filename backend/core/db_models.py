from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Float, Boolean,
    ForeignKey, UniqueConstraint, Index
)

metadata = MetaData()

# 1. Klines
klines = Table(
    'klines',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('asset', String, nullable=False),
    Column('timeframe', String, nullable=False),
    Column('timestamp', Integer, nullable=False),
    Column('open', Float, nullable=False),
    Column('high', Float, nullable=False),
    Column('low', Float, nullable=False),
    Column('close', Float, nullable=False),
    Column('volume', Float, nullable=False),
    UniqueConstraint('asset', 'timeframe', 'timestamp', name='uq_klines_asset_timeframe_timestamp')
)

# 2. News
news = Table(
    'news',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('timestamp', Integer, nullable=False),
    Column('headline', String, nullable=False),
    Column('headline_hash', String, unique=True, nullable=False),
    Column('source', String, nullable=False),
    Column('is_processed', Boolean, server_default='0'),
    Column('processed_at', Integer, nullable=True, server_default=None)
)
Index('idx_news_timestamp', news.c.timestamp)

# 3. Trade Logs
trade_logs = Table(
    'trade_logs',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('timestamp', Integer, nullable=False),
    Column('llm_action', String, nullable=True),
    Column('llm_reasoning', String, nullable=True),
    Column('llm_decision_brief', String, nullable=True),
    Column('action', String, nullable=False),
    Column('llm_conviction', Float, nullable=True),
    Column('system_reliability', Float, nullable=True),
    Column('final_confidence', Float, nullable=True),
    Column('executed_size', Float, nullable=True),
    Column('execution_price', Float, nullable=True),
    Column('reasoning', String, nullable=True),
    Column('payload_snapshot_json', String, nullable=True),
    Column('fee_rate', Float, nullable=True),
    Column('fee_brl', Float, nullable=True),
    Column('slippage_rate', Float, nullable=True),
    Column('expected_price', Float, nullable=True),
    Column('effective_price', Float, nullable=True),
    Column('gross_notional_brl', Float, nullable=True),
    Column('net_notional_brl', Float, nullable=True),
    Column('brl_delta', Float, nullable=True),
    Column('btc_delta', Float, nullable=True),
    Column('equity_before_brl', Float, nullable=True),
    Column('equity_after_brl', Float, nullable=True),
    Column('realized_pnl_brl', Float, nullable=True),
    Column('position_avg_cost_brl', Float, nullable=True)
)
Index('idx_trade_logs_action_timestamp', trade_logs.c.action, trade_logs.c.timestamp)

# 4. Virtual Portfolio
virtual_portfolio = Table(
    'virtual_portfolio',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('currency', String, unique=True, nullable=False),
    Column('amount', Float, nullable=False)
)

# 5. Paper Position State
paper_position_state = Table(
    'paper_position_state',
    metadata,
    Column('asset', String, primary_key=True),
    Column('quantity', Float, nullable=False, server_default='0.0'),
    Column('avg_cost_brl', Float, nullable=False, server_default='0.0'),
    Column('realized_pnl_brl', Float, nullable=False, server_default='0.0'),
    Column('updated_at', Integer, nullable=False)
)

# 6. Paper Position Reconciliations
paper_position_reconciliations = Table(
    'paper_position_reconciliations',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('asset', String, nullable=False),
    Column('timestamp', Integer, nullable=False),
    Column('method', String, nullable=False),
    Column('initial_brl', Float, nullable=False),
    Column('initial_btc', Float, nullable=False),
    Column('reconstructed_brl', Float, nullable=False),
    Column('reconstructed_btc', Float, nullable=False),
    Column('observed_brl', Float, nullable=False),
    Column('observed_btc', Float, nullable=False),
    Column('avg_cost_brl', Float, nullable=False),
    Column('realized_pnl_brl', Float, nullable=False),
    Column('source_log_ids_json', String, nullable=False),
    Column('details_json', String, nullable=False),
)
Index(
    'idx_paper_position_reconciliations_asset_timestamp',
    paper_position_reconciliations.c.asset,
    paper_position_reconciliations.c.timestamp,
)

# 7. System Health
system_health = Table(
    'system_health',
    metadata,
    Column('worker_name', String, primary_key=True),
    Column('last_heartbeat', Integer, nullable=False)
)

# 8. RAG Documents
rag_documents = Table(
    'rag_documents',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('source_type', String, nullable=False),
    Column('source', String, nullable=False),
    Column('title', String, nullable=False),
    Column('content_hash', String, unique=True, nullable=False),
    Column('created_at', Integer, nullable=False),
    Column('published_at', Integer, nullable=True),
    Column('metadata_json', String, nullable=False, server_default="'{}'")
)
Index('idx_rag_documents_published_at', rag_documents.c.published_at)
Index('idx_rag_documents_source_type', rag_documents.c.source_type)

# 9. RAG Chunks
rag_chunks = Table(
    'rag_chunks',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('document_id', Integer, ForeignKey('rag_documents.id'), nullable=False),
    Column('chunk_index', Integer, nullable=False),
    Column('text', String, nullable=False),
    Column('token_estimate', Integer, nullable=False),
    Column('embedding_model', String, nullable=True),
    Column('embedding_vector_json', String, nullable=True),
    Column('metadata_json', String, nullable=False, server_default="'{}'"),
    UniqueConstraint('document_id', 'chunk_index', name='uq_rag_chunks_document_id_chunk_index')
)
Index('idx_rag_chunks_document_id', rag_chunks.c.document_id)

# 10. RAG Retrieval Logs
rag_retrieval_logs = Table(
    'rag_retrieval_logs',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('timestamp', Integer, nullable=False),
    Column('purpose', String, nullable=False),
    Column('query', String, nullable=False),
    Column('filters_json', String, nullable=False, server_default="'{}'"),
    Column('selected_chunk_ids_json', String, nullable=False, server_default="'[]'")
)
