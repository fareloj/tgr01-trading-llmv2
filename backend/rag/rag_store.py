import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Iterable

from core.database import get_connection


@dataclass(frozen=True)
class RagChunk:
    id: int
    document_id: int
    title: str
    source_type: str
    source: str
    published_at: int | None
    chunk_index: int
    text: str
    score: float
    metadata: dict


def init_rag_tables() -> None:
    """Create optional RAG tables without changing trading behavior."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                content_hash TEXT UNIQUE NOT NULL,
                created_at INTEGER NOT NULL,
                published_at INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_estimate INTEGER NOT NULL,
                embedding_model TEXT,
                embedding_vector_json TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(document_id) REFERENCES rag_documents(id),
                UNIQUE(document_id, chunk_index)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_retrieval_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                purpose TEXT NOT NULL,
                query TEXT NOT NULL,
                filters_json TEXT NOT NULL DEFAULT '{}',
                selected_chunk_ids_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_documents_published_at ON rag_documents(published_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_documents_source_type ON rag_documents(source_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_document_id ON rag_chunks(document_id)")
        conn.commit()
    finally:
        conn.close()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    # Conservative enough for budgeting context without adding tokenizer deps.
    return max(1, len(text) // 4)


def chunk_text(text: str, max_chars: int = 1400, overlap_chars: int = 160) -> list[str]:
    """Split text into stable, overlapping chunks."""
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if not normalized:
        return []

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        if end < len(normalized):
            split_at = normalized.rfind("\n", start, end)
            if split_at <= start:
                split_at = normalized.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(0, end - overlap_chars)
        if start > 0:
            boundary = _next_text_boundary(normalized, start, max_scan=80)
            if boundary is not None and boundary < end:
                start = boundary
    return chunks


def _next_text_boundary(text: str, start: int, *, max_scan: int) -> int | None:
    """Move a chunk start away from the middle of a word when possible."""
    scan_end = min(len(text), start + max_scan)
    for index in range(start, scan_end):
        if text[index] in {" ", "\n", "\t"}:
            return index + 1
    return None


def upsert_document(
    *,
    source_type: str,
    source: str,
    title: str,
    text: str,
    published_at: int | None = None,
    metadata: dict | None = None,
) -> int:
    """Insert a document and its chunks. Existing content hash is reused."""
    init_rag_tables()
    doc_hash = content_hash(f"{source_type}\n{source}\n{title}\n{text}")
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM rag_documents WHERE content_hash = ?", (doc_hash,))
        existing = cursor.fetchone()
        if existing:
            return int(existing["id"])

        cursor.execute(
            """
            INSERT INTO rag_documents
                (source_type, source, title, content_hash, created_at, published_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source_type, source, title, doc_hash, int(time.time()), published_at, metadata_json),
        )
        document_id = int(cursor.lastrowid)
        for index, chunk in enumerate(chunk_text(text)):
            cursor.execute(
                """
                INSERT INTO rag_chunks
                    (document_id, chunk_index, text, token_estimate, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, index, chunk, estimate_tokens(chunk), "{}"),
            )
        conn.commit()
        return document_id
    finally:
        conn.close()


def tokenize_query(query: str) -> set[str]:
    tokens = set(re.findall(r"[^\W_]{3,}", query.lower(), flags=re.UNICODE))
    stopwords = {
        "para",
        "com",
        "uma",
        "das",
        "dos",
        "the",
        "and",
        "from",
        "que",
        "por",
        "btc",
    }
    return {token for token in tokens if token not in stopwords}


def _score_text(text: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    normalized = text.lower()
    hits = sum(1 for token in query_tokens if token in normalized)
    density = hits / max(1, len(query_tokens))
    return round(density, 4)


def search_chunks(
    query: str,
    *,
    source_types: Iterable[str] | None = None,
    limit: int = 5,
    max_age_seconds: int | None = None,
    now: int | None = None,
    purpose: str = "manual_review",
    log_retrieval: bool = True,
) -> list[RagChunk]:
    """Deterministic lexical retrieval.

    This is a safe placeholder until embeddings are added. Python controls the
    query, filters and recency window; the LLM never pulls from the store.
    """
    init_rag_tables()
    now = int(now or time.time())
    query_tokens = tokenize_query(query)
    source_type_list = list(source_types or [])

    where = []
    params = []
    if source_type_list:
        placeholders = ",".join("?" for _ in source_type_list)
        where.append(f"d.source_type IN ({placeholders})")
        params.extend(source_type_list)
    if max_age_seconds is not None:
        where.append("(d.published_at IS NULL OR d.published_at >= ?)")
        params.append(now - max_age_seconds)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                c.id AS chunk_id,
                c.document_id,
                c.chunk_index,
                c.text,
                c.metadata_json AS chunk_metadata_json,
                d.title,
                d.source_type,
                d.source,
                d.published_at,
                d.metadata_json AS doc_metadata_json
            FROM rag_chunks c
            JOIN rag_documents d ON d.id = c.document_id
            {where_sql}
            ORDER BY COALESCE(d.published_at, d.created_at) DESC, c.id DESC
            LIMIT 250
            """,
            params,
        )
        scored = []
        for row in cursor.fetchall():
            score = _score_text(row["text"], query_tokens)
            if score <= 0:
                continue
            metadata = {}
            metadata.update(json.loads(row["doc_metadata_json"] or "{}"))
            metadata.update(json.loads(row["chunk_metadata_json"] or "{}"))
            scored.append(
                RagChunk(
                    id=int(row["chunk_id"]),
                    document_id=int(row["document_id"]),
                    title=row["title"],
                    source_type=row["source_type"],
                    source=row["source"],
                    published_at=row["published_at"],
                    chunk_index=int(row["chunk_index"]),
                    text=row["text"],
                    score=score,
                    metadata=metadata,
                )
            )

        scored.sort(key=lambda item: (item.score, item.published_at or 0, item.id), reverse=True)
        selected = scored[:limit]

        if log_retrieval:
            filters = {
                "source_types": source_type_list,
                "limit": limit,
                "max_age_seconds": max_age_seconds,
            }
            cursor.execute(
                """
                INSERT INTO rag_retrieval_logs
                    (timestamp, purpose, query, filters_json, selected_chunk_ids_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now,
                    purpose,
                    query,
                    json.dumps(filters, ensure_ascii=False, sort_keys=True),
                    json.dumps([item.id for item in selected]),
                ),
            )
            conn.commit()

        return selected
    finally:
        conn.close()


def build_context_block(chunks: list[RagChunk], *, title: str = "RAG CONTEXT") -> str:
    """Format retrieved chunks for push-only LLM context."""
    if not chunks:
        return f"[{title}]\nNenhum contexto recuperado.\n"

    lines = [
        f"[{title}]",
        "Contexto recuperado por Python. Use apenas como memoria auxiliar; dados frescos do payload e Risk Manager prevalecem.",
    ]
    for index, chunk in enumerate(chunks, start=1):
        published = chunk.published_at if chunk.published_at is not None else "unknown"
        lines.append(
            f"{index}. source_type={chunk.source_type} source={chunk.source} "
            f"published_at={published} score={chunk.score}"
        )
        lines.append(f"   title={chunk.title}")
        lines.append(f"   text={chunk.text[:900]}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    init_rag_tables()
    print("[OK] RAG tables initialized.")
