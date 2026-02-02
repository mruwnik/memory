import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import anthropic
import openai
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


def pytest_addoption(parser):
    """Add custom command-line options for pytest."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests (database, containers, etc.)",
    )


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks integration tests")
    config.addinivalue_line("markers", "db: marks tests that require database")


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests that use slow fixtures and skip them unless --run-slow is provided."""
    if config.getoption("--run-slow"):
        # --run-slow given: don't skip slow tests
        return

    skip_slow = pytest.mark.skip(reason="need --run-slow option to run")
    slow_fixtures = {"test_db", "db_engine", "db_session", "qdrant"}

    for item in items:
        # Check if test uses any slow fixtures
        if slow_fixtures.intersection(set(getattr(item, "fixturenames", []))):
            item.add_marker(pytest.mark.slow)
            item.add_marker(skip_slow)


class MockRedis:
    """In-memory mock of Redis for testing."""

    _shared_data: dict = {}  # Shared across instances for test isolation

    def __init__(self):
        pass

    @property
    def _data(self):
        return MockRedis._shared_data

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value, nx: bool = False, ex: int | None = None):
        """Set a key with optional NX (only if not exists) and EX (expiry)."""
        if nx and key in self._data:
            return False
        self._data[key] = value
        # Note: expiry is ignored in mock - tests should clean up explicitly
        return True

    def setex(self, key: str, ttl: int, value):
        """Set key with expiry (expiry ignored in mock)."""
        self._data[key] = value
        return True

    def delete(self, *keys: str) -> int:
        """Delete one or more keys. Returns count of deleted keys."""
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count

    def exists(self, *keys: str) -> int:
        """Return count of keys that exist."""
        return sum(1 for key in keys if key in self._data)

    def scan_iter(self, match: str):
        import fnmatch

        pattern = match.replace("*", "**")
        for key in self._data.keys():
            if fnmatch.fnmatch(key, pattern):
                yield key

    @classmethod
    def from_url(cls, url: str):
        return cls()

    @classmethod
    def clear_all(cls):
        """Clear all data - call in test teardown."""
        cls._shared_data.clear()


def get_test_db_name() -> str:
    return f"test_db_{uuid.uuid4().hex[:8]}"


def validate_db_identifier(name: str) -> str:
    """Validate that a database name is a safe SQL identifier.

    Prevents SQL injection by ensuring the name contains only
    alphanumeric characters and underscores.
    """
    import re
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        raise ValueError(f"Invalid database identifier: {name}")
    if len(name) > 63:  # PostgreSQL identifier limit
        raise ValueError(f"Database name too long: {name}")
    return name


def create_test_database(test_db_name: str) -> str:
    """
    Create a test database with a unique name.

    Args:
        test_db_name: Name for the test database

    Returns:
        URL to the test database
    """
    # Validate to prevent SQL injection
    safe_name = validate_db_identifier(test_db_name)
    admin_engine = create_engine(settings.DB_URL)

    # Create a new database
    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # Close any open transaction
        conn.execute(text(f"DROP DATABASE IF EXISTS {safe_name}"))
        conn.execute(text(f"CREATE DATABASE {safe_name}"))

    admin_engine.dispose()

    return settings.make_db_url(db=test_db_name)


def drop_test_database(test_db_name: str) -> None:
    """
    Drop the test database after terminating all active connections.

    Args:
        test_db_name: Name of the test database to drop
    """
    # Validate to prevent SQL injection
    safe_name = validate_db_identifier(test_db_name)
    admin_engine = create_engine(settings.DB_URL)

    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # Close any open transaction

        # Terminate all connections to the database
        conn.execute(
            text(
                f"""
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = '{safe_name}'
                AND pid <> pg_backend_pid()
                """
            )
        )

        # Drop the database
        conn.execute(text(f"DROP DATABASE IF EXISTS {safe_name}"))

    admin_engine.dispose()


def run_alembic_migrations(db_name: str) -> None:
    """Run all Alembic migrations on the test database."""
    project_root = Path(__file__).parent.parent
    alembic_ini = project_root / "db" / "migrations" / "alembic.ini"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": settings.make_db_url(db=db_name)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic migration failed:\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )


@pytest.fixture
def test_db():
    """
    Create a test database, run migrations, and clean up afterwards.

    Returns:
        The URL to the test database
    """
    from memory.common.db import connection as db_connection

    test_db_name = get_test_db_name()

    # Create test database
    try:
        test_db_url = create_test_database(test_db_name)
    except OperationalError as e:
        pytest.skip(f"Failed to create test database: {e}")
        raise  # unreachable, but tells type checker pytest.skip doesn't return

    # Reset the connection module's cached globals so it picks up the new DB_URL
    # This is necessary because make_session() caches the engine globally
    old_engine = db_connection._engine
    old_factory = db_connection._session_factory
    old_scoped = db_connection._scoped_session
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None

    try:
        run_alembic_migrations(test_db_name)

        # Return the URL to the test database
        with patch("memory.common.settings.DB_URL", test_db_url):
            yield test_db_url
    finally:
        # Restore old cached values (or leave as None if they were None)
        db_connection._engine = old_engine
        db_connection._session_factory = old_factory
        db_connection._scoped_session = old_scoped

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
    image_storage_dir = tmp_path / "images"
    image_storage_dir.mkdir(parents=True, exist_ok=True)
    email_storage_dir = tmp_path / "emails"
    email_storage_dir.mkdir(parents=True, exist_ok=True)
    notes_storage_dir = tmp_path / "notes"
    notes_storage_dir.mkdir(parents=True, exist_ok=True)
    comic_storage_dir = tmp_path / "comics"
    comic_storage_dir.mkdir(parents=True, exist_ok=True)
    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "CHUNK_STORAGE_DIR", chunk_storage_dir),
        patch.object(settings, "WEBPAGE_STORAGE_DIR", image_storage_dir),
        patch.object(settings, "EMAIL_STORAGE_DIR", email_storage_dir),
        patch.object(settings, "NOTES_STORAGE_DIR", notes_storage_dir),
        patch.object(settings, "COMIC_STORAGE_DIR", comic_storage_dir),
    ):
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
    def embeder(chunks, *args, **kwargs):
        return Mock(embeddings=[[0.1] * 1024] * len(chunks))

    real_client = voyageai.Client  # type: ignore[reportPrivateImportUsage]
    with patch.object(voyageai, "Client", autospec=True) as mock_client:
        client = mock_client()
        client.real_client = real_client
        client.embed = embeder
        client.multimodal_embed = embeder
        yield client


@pytest.fixture(autouse=True)
def mock_api_keys():
    """Mock API keys and secrets so tests don't fail on missing keys."""
    # Generate a valid 32-byte hex key for encryption (64 hex chars)
    test_encryption_key = "0" * 64
    with (
        patch.object(settings, "ANTHROPIC_API_KEY", "test-anthropic-key"),
        patch.object(settings, "OPENAI_API_KEY", "test-openai-key"),
        patch.object(settings, "SECRETS_ENCRYPTION_KEY", test_encryption_key),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_openai_client():
    with patch.object(openai, "OpenAI", autospec=True) as mock_client:
        client = mock_client()
        client.chat = Mock()

        # Mock non-streaming response
        client.chat.completions.create = Mock(
            return_value=Mock(
                choices=[
                    Mock(
                        message=Mock(
                            content="<summary>test summary</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>"
                        ),
                        finish_reason=None,
                    )
                ],
                usage=Mock(prompt_tokens=10, completion_tokens=20),
            )
        )

        # Store original side_effect for potential override
        def streaming_response(*args, **kwargs):
            if kwargs.get("stream"):
                # Return mock streaming chunks
                return iter(
                    [
                        Mock(
                            choices=[
                                Mock(
                                    delta=Mock(content="test", tool_calls=None),
                                    finish_reason=None,
                                )
                            ],
                            usage=Mock(prompt_tokens=10, completion_tokens=5),
                        ),
                        Mock(
                            choices=[
                                Mock(
                                    delta=Mock(content=" response", tool_calls=None),
                                    finish_reason="stop",
                                )
                            ],
                            usage=Mock(prompt_tokens=10, completion_tokens=15),
                        ),
                    ]
                )
            else:
                # Return non-streaming response
                return Mock(
                    choices=[
                        Mock(
                            message=Mock(
                                content="<summary>test summary</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>"
                            ),
                            finish_reason=None,
                        )
                    ],
                    usage=Mock(prompt_tokens=10, completion_tokens=20),
                )

        client.chat.completions.create.side_effect = streaming_response
        yield client


@pytest.fixture(autouse=True)
def mock_anthropic_client():
    from unittest.mock import AsyncMock

    with patch.object(anthropic, "Anthropic", autospec=True) as mock_client:
        client = mock_client()
        client.messages = Mock()

        # Mock stream as a context manager
        mock_stream = Mock()
        mock_stream.__enter__ = Mock(
            return_value=Mock(
                __iter__=lambda self: iter(
                    [
                        Mock(
                            type="content_block_delta",
                            delta=Mock(
                                type="text_delta",
                                text="<summary>test summary</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>",
                            ),
                        )
                    ]
                )
            )
        )
        mock_stream.__exit__ = Mock(return_value=False)
        client.messages.stream = Mock(return_value=mock_stream)

        client.messages.create = Mock(
            return_value=Mock(
                content=[
                    Mock(
                        text="<summary>test summary</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>"
                    )
                ]
            )
        )

        # Mock async client
        async_client = Mock()
        async_client.messages = Mock()
        async_client.messages.create = AsyncMock(
            return_value=Mock(
                content=[
                    Mock(
                        type="text",
                        text="<summary>test summary</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>",
                    )
                ]
            )
        )

        # Mock async streaming
        def async_stream_ctx(*args, **kwargs):
            async def async_iter():
                yield Mock(
                    type="content_block_delta",
                    delta=Mock(
                        type="text_delta",
                        text="<summary>test summary</summary><tags><tag>tag1</tag><tag>tag2</tag></tags>",
                    ),
                )

            class AsyncStreamMock:
                async def __aenter__(self):
                    return async_iter()

                async def __aexit__(self, *args):
                    pass

            return AsyncStreamMock()

        async_client.messages.stream = Mock(side_effect=async_stream_ctx)

        # Add async_client property to mock
        mock_client.return_value._async_client = None

        with patch.object(anthropic, "AsyncAnthropic", return_value=async_client):
            yield client


@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis client for all tests."""
    import redis

    MockRedis.clear_all()  # Clear before each test
    with patch.object(redis, "Redis", MockRedis), \
         patch.object(redis, "from_url", MockRedis.from_url):
        yield
    MockRedis.clear_all()  # Clear after each test


@pytest.fixture(autouse=True)
def mock_discord_client():
    with patch.object(settings, "DISCORD_NOTIFICATIONS_ENABLED", False):
        yield


# ============================================================================
# Common test helpers
# ============================================================================


def unique_sha256(prefix: str = "") -> bytes:
    """Generate a unique sha256 hash for test data.

    Useful for creating SourceItem instances that require unique sha256 values.
    """
    return hashlib.sha256(f"{prefix}-{uuid.uuid4()}".encode()).digest()


@pytest.fixture
def test_user(db_session):
    """Create a test user for fixtures that need user_id."""
    from memory.common.db.models.users import HumanUser

    user = HumanUser(
        name="Test User",
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(user)
    db_session.commit()
    return user
