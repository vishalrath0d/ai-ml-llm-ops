"""Idempotent seeding of the fictional-product knowledge base.

Safe to call on every startup: documents that already exist (by doc_id) are
skipped, so restarting the container doesn't duplicate chunks in the
persistent Chroma volume.
"""
import logging
from typing import List

from app.ingestion import ingest_document
from app.seed_data import SEED_DOCUMENTS
from app.store import doc_id_exists

logger = logging.getLogger("rag_service.seeding")


def seed_if_missing() -> List[str]:
    """Ingest any seed document not already present. Returns ingested doc_ids."""
    ingested: List[str] = []
    for doc in SEED_DOCUMENTS:
        if doc_id_exists(doc["doc_id"]):
            continue
        ingest_document(
            text=doc["text"],
            doc_id=doc["doc_id"],
            source=doc["source"],
        )
        ingested.append(doc["doc_id"])
    if ingested:
        logger.info("Seeded %d knowledge-base documents: %s", len(ingested), ingested)
    else:
        logger.info("Knowledge base already seeded, nothing to do.")
    return ingested
