"""Shared ingest pipeline: fetch (if URL) -> chunk -> embed -> store.

Used by both the /ingest API endpoint and the standalone seed.py script so
there's exactly one code path for "how a document gets into the store."
"""
import re
import uuid
from typing import Any, Dict, Optional
from urllib.request import urlopen

from app.chunking import chunk_text
from app.embeddings import embed_texts
from app.store import add_chunks

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """Very small HTML-to-text stripper — good enough for teaching purposes.

    Drops <script>/<style> content, strips remaining tags, and collapses
    whitespace. For production use, reach for something like `readability`
    or `trafilatura`; kept dependency-free here on purpose.
    """
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = _TAG_RE.sub(" ", html)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def fetch_url_text(url: str, timeout: float = 10.0) -> str:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - demo/teaching code
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return _html_to_text(raw.decode(charset, errors="replace"))


def ingest_document(
    text: Optional[str] = None,
    url: Optional[str] = None,
    doc_id: Optional[str] = None,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Chunk + embed + store one document. Returns {doc_id, source, chunks_ingested}."""
    if url and not text:
        text = fetch_url_text(url)
        source = source or url

    if not text or not text.strip():
        raise ValueError("no text to ingest (empty text/url content)")

    doc_id = doc_id or str(uuid.uuid4())
    metadata = dict(metadata or {})

    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("chunking produced no chunks")

    embeddings = embed_texts(chunks)

    ids = [f"{doc_id}::chunk-{i}" for i in range(len(chunks))]
    metadatas = [
        {
            **metadata,
            "doc_id": doc_id,
            "source": source,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]

    add_chunks(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)

    return {"doc_id": doc_id, "source": source, "chunks_ingested": len(chunks)}
