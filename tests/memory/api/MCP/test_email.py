"""Tests for Email MCP tools."""

import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock FastMCP before importing
class MockFastMCP:
    def __init__(self, name):
        self.name = name

    def tool(self, **kwargs):
        def decorator(func):
            return func
        return decorator


_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP = MockFastMCP
_mock_fastmcp.server = MagicMock()
_mock_fastmcp.server.dependencies = MagicMock()
_mock_fastmcp.server.dependencies.get_access_token = MagicMock(return_value=None)
sys.modules["fastmcp"] = _mock_fastmcp
sys.modules["fastmcp.server"] = _mock_fastmcp.server
sys.modules["fastmcp.server.dependencies"] = _mock_fastmcp.server.dependencies

# Mock mcp submodules
_mock_mcp = MagicMock()
sys.modules["mcp"] = _mock_mcp
sys.modules["mcp.types"] = MagicMock()
sys.modules["mcp.server"] = MagicMock()
sys.modules["mcp.server.auth"] = MagicMock()
sys.modules["mcp.server.auth.handlers"] = MagicMock()
sys.modules["mcp.server.auth.handlers.authorize"] = MagicMock()
sys.modules["mcp.server.auth.handlers.token"] = MagicMock()
sys.modules["mcp.server.auth.provider"] = MagicMock()
sys.modules["mcp.server.fastmcp"] = MagicMock()
sys.modules["mcp.server.fastmcp.server"] = MagicMock()

_mock_base = MagicMock()
sys.modules["memory.api.MCP.base"] = _mock_base

from memory.common.db import connection as db_connection  # noqa: E402
from memory.common.db.models import EmailAccount, UserSession  # noqa: E402
from memory.common.db.models.users import HumanUser  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.fixture
def test_user(db_session):
    """Create a test user."""
    user = HumanUser.create_with_password(
        email="testuser@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def email_accounts(db_session, test_user):
    """Create test email accounts."""
    accounts = [
        EmailAccount(
            user_id=test_user.id,
            name="Gmail Account",
            email_address="testuser@gmail.com",
            account_type="gmail",
            google_account_id=None,  # Would be linked in real scenario
            active=True,
        ),
        EmailAccount(
            user_id=test_user.id,
            name="Work Account",
            email_address="testuser@work.com",
            account_type="imap",
            imap_server="imap.work.com",
            smtp_server="smtp.work.com",
            smtp_port=587,
            username="testuser@work.com",
            password="workpass",
            active=True,
        ),
        EmailAccount(
            user_id=test_user.id,
            name="Inactive Account",
            email_address="old@example.com",
            account_type="imap",
            imap_server="imap.example.com",
            active=False,
            send_enabled=False,  # Disable sending for this account
        ),
    ]
    db_session.add_all(accounts)
    db_session.commit()
    return accounts


@pytest.fixture
def user_session(db_session, test_user):
    """Create a user session with access token."""
    from datetime import datetime, timedelta, timezone

    session = UserSession(
        id="test-access-token-123",
        user_id=test_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def mock_access_token(user_session):
    """Mock the get_access_token to return our test token."""
    mock_token = Mock()
    mock_token.token = user_session.id

    with patch(
        "memory.api.MCP.servers.email.get_access_token", return_value=mock_token
    ):
        yield mock_token


@pytest.mark.asyncio
async def test_send_email_message_success(
    db_session, test_user, email_accounts, mock_access_token
):
    from memory.api.MCP.servers.email import send_email_message
    from memory.common.email_sender import EmailResult

    # send_email_message is a FunctionTool, get the underlying function
    send_fn = send_email_message.fn if hasattr(send_email_message, 'fn') else send_email_message

    mock_result = EmailResult(success=True, message_id="msg-123")

    with patch("memory.api.MCP.servers.email.make_session") as mock_make_session:
        mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
        mock_make_session.return_value.__exit__ = Mock(return_value=False)

        with patch(
            "memory.api.MCP.servers.email.send_email", return_value=mock_result
        ) as mock_send:
            result = await send_fn(
                to=["recipient@example.com"],
                subject="Test Subject",
                body="Test body",
                from_address="testuser@work.com",
            )

    assert result["success"] is True
    assert result["message_id"] == "msg-123"
    assert result["from"] == "testuser@work.com"
    assert result["to"] == ["recipient@example.com"]
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_message_invalid_from_address(
    db_session, test_user, email_accounts, mock_access_token
):
    from memory.api.MCP.servers.email import send_email_message

    send_fn = send_email_message.fn if hasattr(send_email_message, 'fn') else send_email_message

    with patch("memory.api.MCP.servers.email.make_session") as mock_make_session:
        mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
        mock_make_session.return_value.__exit__ = Mock(return_value=False)

        with pytest.raises(ValueError, match="No active email account found"):
            await send_fn(
                to=["recipient@example.com"],
                subject="Test",
                body="Body",
                from_address="notmyemail@example.com",
            )


@pytest.mark.asyncio
async def test_send_email_message_inactive_account(
    db_session, test_user, email_accounts, mock_access_token
):
    from memory.api.MCP.servers.email import send_email_message

    send_fn = send_email_message.fn if hasattr(send_email_message, 'fn') else send_email_message

    with patch("memory.api.MCP.servers.email.make_session") as mock_make_session:
        mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
        mock_make_session.return_value.__exit__ = Mock(return_value=False)

        with pytest.raises(ValueError, match="No active email account found"):
            await send_fn(
                to=["recipient@example.com"],
                subject="Test",
                body="Body",
                from_address="old@example.com",  # Inactive account
            )


@pytest.mark.asyncio
async def test_send_email_message_empty_recipients(
    db_session, test_user, email_accounts, mock_access_token
):
    from memory.api.MCP.servers.email import send_email_message

    send_fn = send_email_message.fn if hasattr(send_email_message, 'fn') else send_email_message

    with pytest.raises(ValueError, match="At least one recipient is required"):
        await send_fn(
            to=[],
            subject="Test",
            body="Body",
            from_address="testuser@work.com",
        )


@pytest.mark.asyncio
async def test_send_email_message_with_optional_fields(
    db_session, test_user, email_accounts, mock_access_token
):
    from memory.api.MCP.servers.email import send_email_message
    from memory.common.email_sender import EmailResult

    send_fn = send_email_message.fn if hasattr(send_email_message, 'fn') else send_email_message

    mock_result = EmailResult(success=True, message_id="msg-456")

    with patch("memory.api.MCP.servers.email.make_session") as mock_make_session:
        mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
        mock_make_session.return_value.__exit__ = Mock(return_value=False)

        with patch(
            "memory.api.MCP.servers.email.send_email", return_value=mock_result
        ) as mock_send:
            result = await send_fn(
                to=["to@example.com"],
                subject="Test",
                body="Body",
                from_address="testuser@work.com",
                html_body="<p>Body</p>",
                cc=["cc@example.com"],
                bcc=["bcc@example.com"],
                reply_to="reply@example.com",
            )

    assert result["success"] is True
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["cc"] == ["cc@example.com"]
    assert call_kwargs["bcc"] == ["bcc@example.com"]
    assert call_kwargs["html_body"] == "<p>Body</p>"
    assert call_kwargs["reply_to"] == "reply@example.com"


@pytest.mark.asyncio
async def test_send_email_message_send_failure(
    db_session, test_user, email_accounts, mock_access_token
):
    from memory.api.MCP.servers.email import send_email_message
    from memory.common.email_sender import EmailResult

    send_fn = send_email_message.fn if hasattr(send_email_message, 'fn') else send_email_message

    mock_result = EmailResult(success=False, error="SMTP connection failed")

    with patch("memory.api.MCP.servers.email.make_session") as mock_make_session:
        mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
        mock_make_session.return_value.__exit__ = Mock(return_value=False)

        with patch(
            "memory.api.MCP.servers.email.send_email", return_value=mock_result
        ):
            result = await send_fn(
                to=["recipient@example.com"],
                subject="Test",
                body="Body",
                from_address="testuser@work.com",
            )

    assert result["success"] is False
    assert result["error"] == "SMTP connection failed"


@pytest.mark.parametrize(
    "path,should_load",
    [
        ("valid/file.txt", True),
        ("../outside/file.txt", False),  # Path traversal attempt
        ("nonexistent.txt", False),
    ],
)
def test_load_attachment_security(path, should_load, tmp_path):
    from memory.api.MCP.servers.email import _load_attachment
    from memory.common import settings

    # Create a valid file
    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()
    valid_file = valid_dir / "file.txt"
    valid_file.write_text("test content")

    with patch.object(settings, "FILE_STORAGE_DIR", str(tmp_path)):
        result = _load_attachment(path)

    if should_load:
        assert result is not None
        assert result.filename == "file.txt"
        assert result.content == b"test content"
    else:
        assert result is None
