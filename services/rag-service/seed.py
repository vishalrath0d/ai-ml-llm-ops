#!/usr/bin/env python3
"""Standalone seeding script.

Run manually with `python seed.py` to (re-)populate the persistent Chroma
store with the fictional Onwly knowledge base. Idempotent — safe to
re-run; already-ingested doc_ids are skipped. This is the same code path the
FastAPI app runs automatically on startup (see app/main.py's lifespan hook
and AUTO_SEED_ON_STARTUP in app/config.py).
"""
import logging

from app.seeding import seed_if_missing

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ingested = seed_if_missing()
    if ingested:
        print(f"Ingested {len(ingested)} new documents: {ingested}")
    else:
        print("Nothing to do — knowledge base already seeded.")
