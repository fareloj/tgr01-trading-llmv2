import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Iterable, Any

from backend.core import repository


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
    """Initialize the schema using repository."""
    repository.init_db_schema()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
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

    existing = repository.get_document_id_by_hash(doc_hash)
    if existing:
        return int(existing)

    document_id = repository.insert_document(
        source_type=source_type,
        source=source,
        title=title,
        content_hash=doc_hash,
        created_at=int(time.time()),
        published_at=published_at,
        metadata_json=metadata_json
    )

    for index, chunk in enumerate(chunk_text(text)):
        repository.insert_chunk(
            document_id=document_id,
            chunk_index=index,
            text=chunk,
            token_estimate=estimate_tokens(chunk),
            metadata_json="{}"
        )

    return document_id


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
    """Deterministic lexical retrieval."""
    init_rag_tables()
    now = int(now or time.time())
    query_tokens = tokenize_query(query)
    source_type_list = list(source_types or [])

    # Use the central repository joint search function
    rows = repository.search_chunks(
        source_types=source_type_list,
        max_age_seconds=max_age_seconds,
        now=now
    )

    scored = []
    for row in rows:
        score = _score_text(row["text"], query_tokens)
        if score <= 0:
            continue
        meta_dict = {}
        meta_dict.update(json.loads(row["doc_metadata_json"] or "{}"))
        meta_dict.update(json.loads(row["chunk_metadata_json"] or "{}"))
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
                metadata=meta_dict,
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
        repository.insert_retrieval_log(
            timestamp=now,
            purpose=purpose,
            query=query,
            filters_json=json.dumps(filters, ensure_ascii=False, sort_keys=True),
            selected_chunk_ids_json=json.dumps([item.id for item in selected])
        )

    return selected


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
