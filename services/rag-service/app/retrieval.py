"""Hybrid retrieval: this module is the whole point of the teaching example.

Mirrors a real internal data-engineering Slack bot's design: rather than
embedding everything and always doing a vector search (naive RAG — simple,
but imprecise and pays an embedding-compute cost on every query), a small
hand-curated `keyword_triggers.yaml` table is checked first. If the user's
query obviously matches a known topic, we route directly to that document's
chunks — cheaper, deterministic, and more precise than a nearest-neighbor
search for the common cases the team already knows about. Only unmatched,
open-ended queries fall through to full semantic search.

`mode="naive"` skips the keyword table entirely, so a caller can compare both
paths on the same query and literally see them diverge.
"""
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.config import DEFAULT_TOP_K, KEYWORD_TRIGGERS_PATH
from app.embeddings import embed_query
from app.models import RetrievedChunk
from app.store import query as store_query


@lru_cache(maxsize=1)
def _load_keyword_triggers() -> Dict[str, str]:
    """Load {keyword: doc_id} from keyword_triggers.yaml.

    Missing file -> empty table (hybrid mode degrades gracefully to
    always-semantic, it just never finds a keyword match).
    """
    if not os.path.exists(KEYWORD_TRIGGERS_PATH):
        return {}
    with open(KEYWORD_TRIGGERS_PATH, "r") as f:
        raw = yaml.safe_load(f) or {}
    triggers = raw.get("triggers", {})
    # normalize keys to lowercase for case-insensitive substring matching
    return {str(k).lower(): str(v) for k, v in triggers.items()}


def reset_trigger_cache() -> None:
    _load_keyword_triggers.cache_clear()


def match_keyword_trigger(query_text: str) -> Optional[Tuple[str, str]]:
    """Return (matched_keyword, doc_id) for the first keyword found as a
    substring of the (lowercased) query, or None if nothing matches."""
    triggers = _load_keyword_triggers()
    lowered = query_text.lower()
    for keyword, doc_id in triggers.items():
        if keyword in lowered:
            return keyword, doc_id
    return None


def _rows_to_chunks(raw: Dict[str, Any]) -> List[RetrievedChunk]:
    ids = raw.get("ids", [[]])[0]
    documents = raw.get("documents", [[]])[0]
    metadatas = raw.get("metadatas", [[]])[0]
    distances = raw.get("distances", [[]])[0]

    chunks: List[RetrievedChunk] = []
    for chunk_id, doc_text, meta, distance in zip(ids, documents, metadatas, distances):
        meta = meta or {}
        # collection is configured with hnsw:space=cosine, so distance is
        # (1 - cosine_similarity); similarity is the more intuitive score.
        similarity = round(1.0 - float(distance), 4)
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                text=doc_text,
                score=similarity,
                doc_id=meta.get("doc_id", "unknown"),
                source=meta.get("source"),
                chunk_index=meta.get("chunk_index"),
            )
        )
    return chunks


def semantic_search(query_text: str, k: int, where: Optional[Dict[str, Any]] = None) -> List[RetrievedChunk]:
    embedding = embed_query(query_text)
    raw = store_query(embedding, n_results=k, where=where)
    return _rows_to_chunks(raw)


def retrieve(query_text: str, k: int = DEFAULT_TOP_K, mode: str = "hybrid"):
    """Returns (results, retrieval_path, matched_keyword, routed_doc_id)."""
    if mode == "hybrid":
        match = match_keyword_trigger(query_text)
        if match is not None:
            keyword, doc_id = match
            # Still uses semantic ranking, but constrained to the triggered
            # document — the keyword picks *which* doc, embeddings pick the
            # best chunk(s) within it.
            results = semantic_search(query_text, k, where={"doc_id": doc_id})
            return results, "keyword_trigger", keyword, doc_id

    results = semantic_search(query_text, k)
    return results, "semantic", None, None
