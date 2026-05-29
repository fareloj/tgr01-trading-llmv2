# RAG Agent Context Design

This document defines the safe RAG structure for TGR-01 Trading LLM V2.

The RAG layer is intentionally not connected to the trading decision loop yet. It is a preparation layer for future memory, review and study agents.

## Goal

Use retrieval as controlled context, not as an autonomous LLM tool.

The LLM must never decide to search. Python retrieves context deterministically and pushes a bounded block into a future agent prompt.

## Non-Goals

RAG must not:

- calculate indicators;
- calculate position sizing;
- override fresh market data;
- override the Risk Manager;
- send orders;
- make stale historical context look like current evidence;
- let an LLM run arbitrary searches.

## Current Implementation

The current scaffold lives in:

```text
backend/rag/rag_store.py
```

Tables:

- `rag_documents`
- `rag_chunks`
- `rag_retrieval_logs`

Current retrieval:

- lexical/token matching;
- deterministic filters;
- optional source type filter;
- optional max age filter;
- retrieval logs.

Current helper scripts:

```powershell
python .\backend\tests\ingest_rag_sources.py --project-docs --news-hours 24 --news-limit 25
python .\backend\tests\query_rag.py "RSI oversold MACD bearish risk manager" --limit 5
```

Embeddings are intentionally not added yet. The schema already has nullable fields for:

- `embedding_model`
- `embedding_vector_json`

This keeps the future vector step possible without forcing a dependency today.

## Intended Source Types

Suggested `source_type` values:

- `study_note`
- `trade_review`
- `postmortem`
- `market_event`
- `risk_rule`
- `news_summary`
- `architecture_note`

## Safe Usage Pattern

1. Python selects a fixed query.
2. Python applies source and recency filters.
3. Python retrieves a small number of chunks.
4. Python formats a context block.
5. The LLM receives that context as read-only memory.
6. Risk Manager remains final authority.

Example:

```python
from rag.rag_store import search_chunks, build_context_block

chunks = search_chunks(
    "RSI oversold MACD bearish",
    source_types=["study_note", "trade_review"],
    limit=5,
    max_age_seconds=90 * 24 * 3600,
    purpose="review_agent",
)

context = build_context_block(chunks, title="REVIEW MEMORY")
```

## First Practical Uses

### 1. Review Agent Memory

After a 100-cycle paper run, retrieve prior lessons:

- mistakes involving RSI oversold;
- missed upside;
- bad BUY entries;
- stale news blocks;
- cooldown behavior.

Then ask a review LLM to compare the current deterministic report with prior lessons.

### 2. Study Notes

Add curated notes from `crypto_study_plan_for_tgr01.md` into the RAG store.

Use them to help a review agent explain why a decision was risky, without changing live trade logic.

### 3. Trade Postmortems

Store summaries such as:

- "BUY approved during oversold + weak MACD lost 0.8% in 60m."
- "Directional Gate blocked BUY against BEARISH_EXPANDING and avoided downside."

These become searchable memory for future reports.

## Integration Rule

RAG may be used in:

- reports;
- review agents;
- study assistants;
- postmortem generation;
- manual decision support.

RAG must not be used directly in:

- `RiskManager.evaluate_order`;
- paper executor;
- real executor;
- pre-LLM safety gates.

## Future Vector Step

When embeddings are added:

1. keep SQLite metadata as the source of truth;
2. add embedding model name per chunk;
3. keep recency filters mandatory;
4. log selected chunk ids;
5. compare lexical vs vector retrieval before trusting it.

Recommended first vector use:

- review/postmortem retrieval;
- not live trading decisions.
