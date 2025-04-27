"""
Database connection utilities.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session


def get_engine():
    """Create SQLAlchemy engine from environment variables"""
    user = os.getenv("POSTGRES_USER", "kb")
    password = os.getenv("POSTGRES_PASSWORD", "kb")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "kb")
    
    return create_engine(f"postgresql://{user}:{password}@{host}:{port}/{db}")


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


def make_session():
    with get_scoped_session() as session:
        yield session
