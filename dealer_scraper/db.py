"""Database engine and session factory."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

# Local runs use a SQLite file. A scrape thread commits progress every couple of
# seconds while the API reads the same file, so wait for the lock instead of
# failing with "database is locked", and let readers proceed during a write (WAL).
engine = create_engine(
    settings.database_url, pool_pre_ping=True, future=True,
    connect_args={"timeout": 30, "check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
