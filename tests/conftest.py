from datetime import datetime
import os
import subprocess
from unittest.mock import patch
import uuid
from pathlib import Path

import pytest
import qdrant_client
import voyageai
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from testcontainers.qdrant import QdrantContainer

from memory.common import settings
from memory.common.qdrant import initialize_collections
from tests.providers.email_provider import MockEmailProvider


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
    Drop the test database after terminating all active connections.

    Args:
        test_db_name: Name of the test database to drop
    """
    admin_engine = create_engine(settings.DB_URL)

    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # Close any open transaction

        # Terminate all connections to the database
        conn.execute(
            text(
                f"""
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = '{test_db_name}'
                AND pid <> pg_backend_pid()
                """
            )
        )

        # Drop the database
        conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))

    admin_engine.dispose()


def run_alembic_migrations(db_name: str) -> None:
    """Run all Alembic migrations on the test database."""
    project_root = Path(__file__).parent.parent
    alembic_ini = project_root / "db" / "migrations" / "alembic.ini"

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
    try:
        test_db_url = create_test_database(test_db_name)
    except OperationalError as e:
        pytest.skip(f"Failed to create test database: {e}")

    try:
        run_alembic_migrations(test_db_name)

        # Return the URL to the test database
        with patch("memory.common.settings.DB_URL", test_db_url):
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


@pytest.fixture
def email_provider():
    return MockEmailProvider(
        emails_by_folder={
            "INBOX": [
                {
                    "uid": 101,
                    "flags": "\\Seen",
                    "date": datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "date_internal": datetime.now().strftime("%d-%b-%Y %H:%M:%S +0000"),
                    "from": "alice@example.com",
                    "to": "bob@example.com",
                    "subject": "Test Email 1",
                    "message_id": "<test-101@example.com>",
                    "body": "This is test email 1",
                },
                {
                    "uid": 102,
                    "flags": "",
                    "date": datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "date_internal": datetime.now().strftime("%d-%b-%Y %H:%M:%S +0000"),
                    "from": "charlie@example.com",
                    "to": "bob@example.com",
                    "subject": "Test Email 2",
                    "message_id": "<test-102@example.com>",
                    "body": "This is test email 2",
                },
            ],
            "Archive": [
                {
                    "uid": 201,
                    "flags": "\\Seen",
                    "date": datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "date_internal": datetime.now().strftime("%d-%b-%Y %H:%M:%S +0000"),
                    "from": "dave@example.com",
                    "to": "bob@example.com",
                    "subject": "Archived Email",
                    "message_id": "<test-201@example.com>",
                    "body": "This is an archived email",
                }
            ],
        }
    )


@pytest.fixture(autouse=True)
def mock_file_storage(tmp_path: Path):
    chunk_storage_dir = tmp_path / "chunks"
    chunk_storage_dir.mkdir(parents=True, exist_ok=True)
    with patch("memory.common.settings.FILE_STORAGE_DIR", tmp_path):
        with patch("memory.common.settings.CHUNK_STORAGE_DIR", chunk_storage_dir):
            yield


@pytest.fixture
def qdrant():
    with QdrantContainer() as qdrant:
        client = qdrant.get_client()
        with patch.object(qdrant_client, "QdrantClient", return_value=client):
            initialize_collections(client)
            yield client


@pytest.fixture(autouse=True)
def mock_voyage_client():
    with patch.object(voyageai, "Client", autospec=True) as mock_client:
        client = mock_client()
        client.embed.return_value.embeddings = [[0.1] * 1024]
        client.multimodal_embed.return_value.embeddings = [[0.1] * 1024]
        yield client
