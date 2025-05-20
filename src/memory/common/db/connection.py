"""
Database connection utilities.
"""

from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

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
    engine = get_engine()
    session_factory = sessionmaker(bind=engine)
    return scoped_session(session_factory)


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
