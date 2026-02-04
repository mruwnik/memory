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
        _engine = create_engine(
            settings.DB_URL,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,   # Recycle connections after 1 hour
        )
    return _engine


def get_session_factory():
    """Get or create a cached session factory for SQLAlchemy sessions."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = sessionmaker(bind=engine)
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
def make_session() -> Generator[scoped_session[Session], None, None]:
    """
    Context manager for database sessions.

    Uses a scoped_session which provides thread-local session management.
    The session is committed on success, rolled back on exception.

    Note: We don't call session.remove() here because that would invalidate
    the session for the entire thread/scope, breaking nested session usage.
    The scoped_session handles cleanup automatically when the scope ends.

    Yields:
        SQLAlchemy session that will be automatically committed/rolled back
    """
    session = get_scoped_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
