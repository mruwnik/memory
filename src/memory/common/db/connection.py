"""
Database connection utilities.
"""

from contextlib import contextmanager
from typing import Generator, TypeAlias
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, Session

from memory.common import settings

# Type alias for functions that accept either a regular Session or a scoped_session
# This is useful because scoped_session proxies to Session but has a different type
DBSession: TypeAlias = Session | scoped_session[Session]

# Postgres-side safety net: any session that sits "idle in transaction" longer
# than this gets killed by the server, so a leaked Session can't permanently
# pin a pooled connection. Combined with pool_pre_ping below, the pool then
# transparently replaces the dead connection on next checkout.
IDLE_IN_TRANSACTION_TIMEOUT_MS = 60_000

# Cached engine and session factory for connection pooling
_engine = None
_session_factory = None
_scoped_session = None


def get_engine():
    """Get or create SQLAlchemy engine with connection pooling.

    The engine is cached to ensure connection pooling works correctly.
    Creating a new engine for each request would bypass the pool.
    """
    global _engine
    if _engine is None:
        connect_args: dict = {}
        if settings.DB_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
            # -c options=... is psycopg2's way to set GUCs at connect time
            connect_args["options"] = (
                f"-c idle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_TIMEOUT_MS}"
            )
        _engine = create_engine(
            settings.DB_URL,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,   # Recycle connections after 1 hour
            connect_args=connect_args,
        )
    return _engine


def get_session_factory():
    """Get or create a cached session factory for SQLAlchemy sessions.

    expire_on_commit=False: keep loaded attributes valid after commit().
    The default (True) causes a subsequent attribute read to issue a fresh
    SELECT which auto-begins a new transaction. If the surrounding code
    then returns/raises before the session's own commit/close, that
    transaction can leak as `idle in transaction` and pin the connection.
    """
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def get_scoped_session():
    """Get or create a thread-local scoped session factory."""
    global _scoped_session
    if _scoped_session is None:
        _scoped_session = scoped_session(get_session_factory())
    return _scoped_session


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency for database sessions"""
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def make_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.

    Creates a plain session, commits on success, rolls back on exception,
    and always closes to return the connection to the pool.

    Yields:
        SQLAlchemy session that will be automatically committed/rolled back
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
