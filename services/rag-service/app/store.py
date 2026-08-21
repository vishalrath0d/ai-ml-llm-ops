"""Persistent, embedded ChromaDB wrapper (no separate Chroma server needed).

`chromadb.PersistentClient` writes its SQLite + HNSW index files to
CHROMA_PERSIST_DIR, so as long as that path is a mounted volume, ingested
data survives container restarts.
"""
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import chromadb

from app.config import CHROMA_PERSIST_DIR, COLLECTION_NAME


@lru_cache(maxsize=1)
def _get_client() -> "chromadb.ClientAPI":
    os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
    # anonymized_telemetry=False: chromadb 0.5.23's bundled posthog telemetry
    # calls capture() with a signature the installed posthog version doesn't
    # accept, spamming "ERROR:chromadb.telemetry.product.posthog:Failed to
    # send telemetry event" on every collection/query call. It's a harmless
    # version mismatch either way, but a local project has no reason to
    # phone home at all -- disable it outright instead of living with the
    # log noise.
    return chromadb.PersistentClient(
        path=CHROMA_PERSIST_DIR,
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )


def _get_collection():
    client = _get_client()
    # cosine similarity space so that "1 - distance" is a directly usable
    # similarity score in the API response.
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def reset_client_cache() -> None:
    """Used by tests to point the store at a fresh temp directory."""
    _get_client.cache_clear()


def add_chunks(
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
) -> None:
    if not ids:
        return
    collection = _get_collection()
    collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def query(
    query_embedding: List[float],
    n_results: int,
    where: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, count),
        where=where,
    )


def count() -> int:
    return _get_collection().count()


def doc_id_exists(doc_id: str) -> bool:
    collection = _get_collection()
    result = collection.get(where={"doc_id": doc_id}, limit=1)
    return len(result.get("ids", [])) > 0
