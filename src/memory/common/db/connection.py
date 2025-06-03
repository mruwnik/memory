"""
Database connection utilities.
"""

from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, Session

from memory.common import settings


def get_engine():
    """Create SQLAlchemy engine from environment variables"""
    return create_engine(settings.DB_URL)


def get_session_factory():
    """Create a session factory for SQLAlchemy sessions"""
    engine = get_engine()
    session_factory = sessionmaker(bind=engine)
    return session_factory


def get_scoped_session():
    """Create a thread-local scoped session factory"""
    return scoped_session(get_session_factory())


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency for database sessions"""
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def make_session():
    """
    Context manager for database sessions.

    Yields:
        SQLAlchemy session that will be automatically closed
    """
    session = get_scoped_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.remove()
