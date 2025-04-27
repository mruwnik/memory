import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from memory.common import settings


def get_test_db_name() -> str:
    return f"test_db_{uuid.uuid4().hex[:8]}"


def create_test_database(test_db_name: str) -> str:
    """
    Create a test database with a unique name.
    
    Args:
        test_db_name: Name for the test database
        
    Returns:
        URL to the test database
    """
    admin_engine = create_engine(settings.DB_URL)
    
    # Create a new database
    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # Close any open transaction
        conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
        conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    
    admin_engine.dispose()
    
    return settings.make_db_url(db=test_db_name)


def drop_test_database(test_db_name: str) -> None:
    """
    Drop the test database.
    
    Args:
        test_db_name: Name of the test database to drop
    """
    admin_engine = create_engine(settings.DB_URL)
    
    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # Close any open transaction
        conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))


def run_alembic_migrations(db_name: str) -> None:
    """Run all Alembic migrations on the test database."""
    project_root = Path(__file__).parent.parent.parent.parent.parent
    alembic_ini = project_root / "db" / "migrations" / "alembic.ini"
    
    breakpoint()
    subprocess.run(
        ["alembic", "-c", str(alembic_ini), "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": settings.make_db_url(db=db_name)},
        check=True,
        capture_output=True,
    )


@pytest.fixture
def test_db():
    """
    Create a test database, run migrations, and clean up afterwards.
    
    Returns:
        The URL to the test database
    """
    test_db_name = get_test_db_name()
    
    # Create test database
    test_db_url = create_test_database(test_db_name)
    
    try:
        run_alembic_migrations(test_db_name)
        
        # Return the URL to the test database
        yield test_db_url
    finally:
        # Clean up - drop the test database
        drop_test_database(test_db_name)


@pytest.fixture
def db_engine(test_db):
    """
    Create a SQLAlchemy engine connected to the test database.
    
    Args:
        test_db: URL to the test database (from the test_db fixture)
        
    Returns:
        SQLAlchemy engine
    """
    engine = create_engine(test_db)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """
    Create a new database session for a test.
    
    Args:
        db_engine: SQLAlchemy engine (from the db_engine fixture)
        
    Returns:
        SQLAlchemy session
    """
    # Create a new sessionmaker
    SessionLocal = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    
    # Create a new session
    session = SessionLocal()
    
    try:
        yield session
    finally:
        # Close and rollback the session after the test is done
        session.rollback()
        session.close()
