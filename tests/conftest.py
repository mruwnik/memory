import hashlib
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import anthropic
import openai
import pytest
import qdrant_client
import qdrant_client.models as qdrant_models
import voyageai
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from testcontainers.qdrant import QdrantContainer

from memory.common import settings
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
    config.addinivalue_line(
        "markers",
        "transactional_db: opt out of the SAVEPOINT-based db_session pattern. "
        "Each test gets a real session that commits to the DB; teardown "
        "TRUNCATEs all tables. ~2 s slower per test but works with code "
        "paths that conflict with the shared-connection SAVEPOINT trick.",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests that use slow fixtures and skip them unless --run-slow is provided."""
    if config.getoption("--run-slow"):
        # --run-slow given: don't skip slow tests
        return

    skip_slow = pytest.mark.skip(reason="need --run-slow option to run")
    slow_fixtures = {"test_db", "db_engine", "db_session", "qdrant", "qdrant_container"}

    for item in items:
        # Check if test uses any slow fixtures
        if slow_fixtures.intersection(set(getattr(item, "fixturenames", []))):
            item.add_marker(pytest.mark.slow)
            item.add_marker(skip_slow)


class _MockRedisPipeline:
    """No-op pipeline that delegates to the underlying mock."""

    def __init__(self, client: "MockRedis"):
        self._client = client
        self._results: list = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        # Buffer operations and return self to support method chaining; on
        # execute() we return the recorded results.
        method = getattr(self._client, name, None)
        if method is None:
            raise AttributeError(name)

        def wrapper(*args, **kwargs):
            self._results.append(method(*args, **kwargs))
            return self

        return wrapper

    def execute(self):
        results = self._results
        self._results = []
        return results


class MockRedis:
    """In-memory mock of Redis for testing."""

    _shared_data: dict = {}  # Shared across instances for test isolation

    def __init__(self, *args, **kwargs):
        # Accept and ignore real-Redis kwargs (host, port, connection_pool, etc.)
        pass

    def ping(self):
        return True

    def info(self, *args, **kwargs):
        return {}

    def keys(self, pattern: str = "*"):
        return list(self.scan_iter(pattern))

    def expire(self, key: str, seconds: int) -> int:
        return 1 if key in self._data else 0

    def ttl(self, key: str) -> int:
        return -1 if key in self._data else -2

    def incr(self, key: str, amount: int = 1) -> int:
        current = int(self._data.get(key, 0))
        current += amount
        self._data[key] = current
        return current

    def llen(self, key: str) -> int:
        """List length — return 0 for any key (queues are empty in mock)."""
        v = self._data.get(key, [])
        if isinstance(v, list):
            return len(v)
        return 0

    def pipeline(self, *args, **kwargs):
        # Simple no-op pipeline that just executes commands directly.
        return _MockRedisPipeline(self)

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

    def eval(self, script: str, numkeys: int, *args):
        """Minimal Lua-script emulation for the CAS-delete and CAS-extend
        patterns used by lock-release helpers. Parses the script text for
        the operation keyword rather than running real Lua."""
        keys = args[:numkeys]
        argv = args[numkeys:]
        key = keys[0]
        expected_value = argv[0]
        current = self._data.get(key)
        if current is None or str(current) != str(expected_value):
            return 0
        # Distinguish del vs expire/pexpire by inspecting the script body
        s = script.lower()
        if "del" in s and "expire" not in s:
            del self._data[key]
            return 1
        if "expire" in s or "pexpire" in s:
            # TTL is a no-op in the mock
            return 1
        # Unknown script — be conservative and return 0
        return 0

    @classmethod
    def from_url(cls, url: str, *args, **kwargs):
        # Real ``redis.Redis.from_url`` accepts socket timeouts and other
        # connection kwargs. Swallow them silently — they're irrelevant
        # to the in-memory mock and rejecting them blows up callers
        # (notably ``rate_limit.get_redis``) with TypeError.
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

    # Create a new database - must use autocommit for DDL
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # Terminate any stale connections from previous crashed runs
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


@pytest.fixture(scope="session")
def test_db():
    """
    Create a test database ONCE per session, run migrations, clean up at end.

    Individual test isolation is achieved via table truncation in db_session.
    """
    from memory.common.db import connection as db_connection

    # Unique DB per pytest invocation (PID-suffixed) so concurrent runs don't
    # drop each other's databases via the create_test_database cleanup. Also
    # carries the xdist worker id for parallel runs within a single invocation.
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    test_db_name = f"test_memory_session_{worker_id}_{os.getpid()}"

    # Create test database
    try:
        test_db_url = create_test_database(test_db_name)
    except OperationalError as e:
        pytest.skip(f"Failed to create test database: {e}")
        raise

    # Reset the connection module's cached globals
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None

    try:
        run_alembic_migrations(test_db_name)
        with patch("memory.common.settings.DB_URL", test_db_url):
            yield test_db_url
    finally:
        db_connection._engine = None
        db_connection._session_factory = None
        db_connection._scoped_session = None
        drop_test_database(test_db_name)


@pytest.fixture(scope="session")
def db_engine(test_db):
    """Create a SQLAlchemy engine for the test DB (session-scoped).

    Uses NullPool so each test connection is closed for real (not returned to a
    pool). This matters with the SAVEPOINT-rollback pattern in db_session: if a
    test's inner make_session() leaves the connection in "idle in transaction
    (aborted)" state, a pooled connection would carry that bad state into the
    next test and eventually deadlock on row locks.
    """
    from sqlalchemy.pool import NullPool

    engine = create_engine(test_db, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(request, db_engine):
    """Create a session for a test.

    Default: SAVEPOINT-on-shared-connection pattern (15× faster teardown than
    TRUNCATE). The connection module's session factory is redirected so that
    production code's ``make_session()`` shares the test connection — letting
    the test see production-side writes and vice versa without committing to
    the real DB.

    Mark a test with ``@pytest.mark.transactional_db`` to opt into a
    real-commit, TRUNCATE-cleanup variant. Use that for tests where
    production code's session lifecycle conflicts with the SAVEPOINT pattern
    (typical symptom: ``IllegalStateChangeError`` during commit, or a test
    asserting on ``session.in_transaction()``).
    """
    if request.node.get_closest_marker("transactional_db") is not None:
        yield from _transactional_db_session(db_engine)
        return

    from memory.common.db import connection as db_connection

    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = SessionLocal()

    nested = connection.begin_nested()

    # Listen on the sessionmaker so the savepoint restarts after *any*
    # session bound to this connection ends a transaction — including inner
    # sessions opened by production code via make_session(). Without this,
    # the inner commit consumes the savepoint and subsequent statements on
    # the connection fail with "nested transaction already deassociated".
    #
    # Only restart when the outermost SAVEPOINT-level transaction ends, so
    # we don't recursively restart while production code is mid-flush.
    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            try:
                nested = connection.begin_nested()
            except Exception:
                pass

    saved_factory = db_connection._session_factory
    saved_engine = db_connection._engine
    saved_scoped = db_connection._scoped_session
    db_connection._session_factory = SessionLocal
    db_connection._engine = None
    db_connection._scoped_session = None

    try:
        yield session
    finally:
        db_connection._session_factory = saved_factory
        db_connection._engine = saved_engine
        db_connection._scoped_session = saved_scoped
        try:
            session.close()
        except Exception:
            pass
        try:
            if transaction.is_active:
                transaction.rollback()
        except Exception:
            pass
        connection.close()


def _transactional_db_session(db_engine):
    """Generator backing the @pytest.mark.transactional_db opt-out path."""
    SessionLocal = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        with db_engine.connect() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname='public' AND tablename != 'alembic_version'"
                    )
                )
            ]
            if tables:
                conn.execute(text("SET session_replication_role = 'replica'"))
                table_list = ", ".join(f'"{t}"' for t in tables)
                conn.execute(
                    text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
                )
                conn.execute(text("SET session_replication_role = 'origin'"))
                conn.commit()


# =============================================================================
# MCP Server Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    """Keep tests off a real Celery broker.

    Code paths that dispatch tasks — notably the ``after_commit`` listener in
    ``models/access_control_events.py``, which fires on every data-source
    config change — would otherwise call ``app.send_task`` and try to reach
    the broker host. Patch it to a Mock for every test; tests that want to
    assert on dispatch can take this fixture and inspect the returned mock.
    """
    from memory.common.celery_app import app

    with patch.object(app, "send_task") as mock_send_task:
        yield mock_send_task


@pytest.fixture(scope="session")
def mcp_servers():
    """Pre-load MCP server modules once per test session.

    This avoids the cost of importing MCP modules for each test.
    The first import loads FastMCP decorators and sets up the tools.
    """
    from memory.api.MCP.servers import people, teams, discord

    return {
        "people": people,
        "teams": teams,
        "discord": discord,
    }


@contextmanager
def mcp_auth_context(session_token: str):
    """Set up FastMCP auth context for testing.

    This sets the auth_context_var that FastMCP's get_access_token() reads from,
    allowing tests to run without mocking internal functions.

    Usage:
        with mcp_auth_context(admin_session.id):
            result = await some_mcp_tool(...)
    """
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    access_token = AccessToken(
        token=session_token,
        client_id="test-client",
        scopes=[],
    )
    auth_user = AuthenticatedUser(access_token)
    token = auth_context_var.set(auth_user)
    try:
        yield
    finally:
        auth_context_var.reset(token)


@pytest.fixture
def admin_user(db_session):
    """Create an admin user with superadmin scope."""
    from memory.common.db.models import HumanUser

    user = HumanUser(
        name="Admin User",
        email="admin@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["*"],  # Admin scope
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def regular_user(db_session):
    """Create a regular user without admin scope."""
    from memory.common.db.models import HumanUser

    user = HumanUser(
        name="Regular User",
        email="regular@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["teams"],  # Only teams scope, not admin
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def admin_session(db_session, admin_user):
    """Create a user session for the admin user."""
    from memory.common.db.models import UserSession

    session = UserSession(
        id="admin-session-token",
        user_id=admin_user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def user_session(db_session, regular_user):
    """Create a user session for the regular user."""
    from memory.common.db.models import UserSession

    session = UserSession(
        id="test-session-token",
        user_id=regular_user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


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
    report_storage_dir = tmp_path / "reports"
    report_storage_dir.mkdir(parents=True, exist_ok=True)
    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "CHUNK_STORAGE_DIR", chunk_storage_dir),
        patch.object(settings, "WEBPAGE_STORAGE_DIR", image_storage_dir),
        patch.object(settings, "EMAIL_STORAGE_DIR", email_storage_dir),
        patch.object(settings, "NOTES_STORAGE_DIR", notes_storage_dir),
        patch.object(settings, "COMIC_STORAGE_DIR", comic_storage_dir),
        patch.object(settings, "REPORT_STORAGE_DIR", report_storage_dir),
    ):
        yield


class _ResilientQdrantContainer:
    """Wraps a QdrantContainer and lazily (re)starts the underlying Docker
    container if it dies between tests. Long test runs were hitting transient
    container failures that cascaded into 200+ setup errors otherwise."""

    def __init__(self):
        self._container: QdrantContainer | None = None
        self._client = None

    def _start(self):
        container = QdrantContainer()
        container.__enter__()
        self._container = container
        # 30s timeout (default is 5s) — under -n 4 xdist, 4 qdrant containers
        # share the host's docker engine and CPU, and the 5s default tripped
        # cascading ReadTimeouts on writes.
        client = container.get_client(timeout=30)
        self._client = client
        from memory.common.collections import ALL_COLLECTIONS
        from memory.common.qdrant import ensure_collection_exists
        from concurrent.futures import ThreadPoolExecutor

        # Wipe any pre-existing collections left from a prior container life.
        for col in client.get_collections().collections:
            client.delete_collection(col.name)

        def _init(name_params):
            name, params = name_params
            ensure_collection_exists(
                client,
                collection_name=name,
                dimension=params["dimension"],
                distance=params.get("distance", "Cosine"),
                on_disk=params.get("on_disk", True),
                shards=params.get("shards", 1),
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(_init, ALL_COLLECTIONS.items()))

    def get_client(self):
        if self._client is None:
            self._start()
        else:
            try:
                self._client.get_collections()
            except Exception:
                # Container died — tear down and rebuild.
                try:
                    if self._container is not None:
                        self._container.__exit__(None, None, None)
                except Exception:
                    pass
                self._container = None
                self._client = None
                self._start()
        return self._client

    def stop(self):
        if self._container is not None:
            try:
                self._container.__exit__(None, None, None)
            except Exception:
                pass
            self._container = None
            self._client = None


@pytest.fixture(scope="session")
def qdrant_container():
    """Session-scoped Qdrant container with auto-restart on death."""
    container = _ResilientQdrantContainer()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def _qdrant_initialized(qdrant_container):
    """Initialize collections once per session — recreating ~17 collections
    per test was costing 9–20 s of setup time per qdrant-using test."""
    return qdrant_container.get_client()


def _ensure_qdrant_alive(client) -> bool:
    """Quick liveness probe. Returns False if the qdrant container is gone."""
    try:
        client.get_collections()
        return True
    except Exception:
        return False


@pytest.fixture
def qdrant(qdrant_container):
    """Function-scoped Qdrant client. Collections persist across tests; we
    just clear points between tests for isolation.

    Calls back into ``qdrant_container.get_client()`` so that a container
    that died between tests is rebuilt (collections re-initialized) before
    the test sees it.
    """
    client = qdrant_container.get_client()

    for collection in client.get_collections().collections:
        try:
            info = client.get_collection(collection.name)
        except Exception:
            continue  # transient; will be re-checked next test
        if info.points_count == 0:
            continue
        # Capped scroll: gather point IDs in batches, delete in batches.
        offset = None
        while True:
            try:
                points, offset = client.scroll(
                    collection_name=collection.name,
                    limit=1024,
                    offset=offset,
                    with_payload=False,
                    with_vectors=False,
                )
            except Exception:
                break
            if not points:
                break
            client.delete(
                collection_name=collection.name,
                points_selector=qdrant_models.PointIdsList(
                    points=[p.id for p in points]
                ),
            )
            if offset is None:
                break

    with patch.object(qdrant_client, "QdrantClient", return_value=client):
        yield client


@pytest.fixture(autouse=True)
def mock_voyage_client():
    def embeder(chunks, *args, **kwargs):
        return Mock(embeddings=[[0.1] * 1024] * len(chunks))

    real_client = voyageai.Client  # type: ignore[reportPrivateImportUsage]
    with patch.object(voyageai, "Client") as mock_client:
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
    with patch.object(openai, "OpenAI") as mock_client:
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

    with patch.object(anthropic, "Anthropic") as mock_client:
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


@pytest.fixture(autouse=True)
def _stub_ssrf_validation():
    """Stub validate_public_url so tests don't hit real DNS resolution.

    Production blocks non-public IPs; in tests every URL we use is fictitious
    so the check would always reject — turn it into a no-op. Tests that
    specifically want to verify SSRF rejection patch the function themselves
    at their own call site (which takes precedence over this autouse stub).
    """
    import importlib
    from memory.common import ssrf

    def noop(url):
        return None

    # Patch every module that imports it by-name so the binding inside that
    # module sees the no-op (`from memory.common.ssrf import validate_public_url`
    # is the common pattern).
    targets = [
        (ssrf, "validate_public_url"),
    ]
    for module_name in (
        "memory.api.article_feeds",
        "memory.workers.tasks.blogs",
    ):
        try:
            mod = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(mod, "validate_public_url"):
            targets.append((mod, "validate_public_url"))

    from contextlib import ExitStack

    with ExitStack() as stack:
        for mod, attr in targets:
            stack.enter_context(patch.object(mod, attr, noop))
        yield


@pytest.fixture(scope="session", autouse=True)
def _fast_bcrypt():
    """Drop bcrypt cost factor from 12 (production) to 4 (test).

    240ms -> 1ms per hash. The hash output remains a valid $2b$ bcrypt string
    so the format and verification tests still pass. Real cost-factor behavior
    is untested, but those bytes-correct properties aren't what test_users
    is exercising — it's verifying API surface (verify roundtrip, format).
    """
    import bcrypt

    real_gensalt = bcrypt.gensalt

    def fast_gensalt(rounds: int = 12, **kwargs):
        return real_gensalt(rounds=4, **kwargs)

    with patch.object(bcrypt, "gensalt", fast_gensalt):
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


@pytest.fixture
def sample_user(test_user):
    """Alias for test_user for compatibility with existing tests."""
    return test_user
