#!/usr/bin/env python3
"""Standalone seed script for eval-service demo scenarios.

Run manually, e.g. inside the container or locally with DATABASE_URL pointed
at eval-service's Postgres:

    python seed.py

This is also invoked automatically at API startup by app/main.py's lifespan
hook (guarded by seed_if_empty, so re-running -- container restarts included
-- never creates duplicate scenarios).
"""
from app.database import Base, SessionLocal, engine
from app.seed import seed_if_empty


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        inserted = seed_if_empty(db)
        print(f"Seeded {inserted} scenario(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
