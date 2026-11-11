"""ERROR records reach the appliance operator, so they carry ids, never content."""

import ast
import logging
import pathlib
import smtplib
from types import SimpleNamespace

import pytest

from memory.common import extract
from memory.common.email_sender import SmtpConfig, send_via_smtp
from memory.common.github import GithubClient, GithubCredentials

SECRET = "confidential-merger-plan"
SRC = pathlib.Path(__file__).parents[3] / "src" / "memory"
OPERATOR_LEVELS = {"error", "critical", "exception"}
EXCEPTION_NAMES = {"e", "ex", "exc", "err", "error", "exception"}


def error_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_docx_to_pdf_failure_keeps_document_text_out_of_error_log(
    caplog, monkeypatch, tmp_path
):
    def fail(*args, **kwargs):
        raise RuntimeError(f"! LaTeX Error. l.12 {SECRET}")

    monkeypatch.setattr(extract, "PANDOC_PATH", "pandoc")
    monkeypatch.setattr(extract, "run_pandoc", fail)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        extract.docx_to_pdf(pathlib.Path(tmp_path / "a.docx"), tmp_path / "a.pdf")

    messages = error_messages(caplog)
    assert messages
    assert not [m for m in messages if SECRET in m]


class FailingSMTP:
    def __init__(self, error: Exception):
        self.error = error

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, **kwargs):
        pass

    def login(self, *args):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPAuthenticationError(535, f"bad password for {SECRET}@example.com".encode()),
        smtplib.SMTPException(f"550 rejected {SECRET}@example.com"),
        RuntimeError(f"connection to {SECRET}@example.com dropped"),
    ],
)
def test_smtp_failure_keeps_mailbox_out_of_error_log(caplog, monkeypatch, error):
    monkeypatch.setattr(
        "memory.common.email_sender.mail_connect.PublicSMTP", FailingSMTP(error)
    )
    config = SmtpConfig(
        email_address=f"{SECRET}@example.com",
        username=None,
        password="pw",
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    with caplog.at_level(logging.ERROR):
        result = send_via_smtp(config=config, to=["to@example.com"], subject="s", body="b")

    assert result.success is False
    messages = error_messages(caplog)
    assert messages
    assert not [m for m in messages if SECRET in m]


def github_client(monkeypatch, response) -> GithubClient:
    client = GithubClient(GithubCredentials(auth_type="pat", access_token="token"))
    monkeypatch.setattr(client.session, "post", lambda *a, **kw: response)
    monkeypatch.setattr(client.session, "patch", lambda *a, **kw: response)
    return client


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.create_milestone("owner", SECRET, SECRET),
        lambda c: c.update_milestone("owner", SECRET, 7, title=SECRET),
        lambda c: c.create_repository(SECRET, org="owner"),
    ],
)
def test_github_write_failure_keeps_titles_and_response_out_of_error_log(
    caplog, monkeypatch, call
):
    response = SimpleNamespace(
        ok=False, status_code=422, text=f'{{"message": "Validation Failed", "title": "{SECRET}"}}'
    )
    client = github_client(monkeypatch, response)

    with caplog.at_level(logging.ERROR):
        assert call(client) is None

    messages = error_messages(caplog)
    assert messages
    assert not [m for m in messages if SECRET in m]


def leaky_value(node: ast.expr) -> bool:
    """An exception, its text, or a URL: what an ERROR message line must not carry."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"str", "repr"}:
        return any(leaky_value(arg) for arg in node.args)
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
    return name in EXCEPTION_NAMES or name.lower().endswith("url")


def message_values(call: ast.Call) -> list[ast.expr]:
    """The values formatted into a log call's message: f-string fields and %-args."""
    fields = [
        part.value
        for arg in call.args[:1]
        if isinstance(arg, ast.JoinedStr)
        for part in arg.values
        if isinstance(part, ast.FormattedValue)
    ]
    return fields + list(call.args[1:])


def operator_log_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in OPERATOR_LEVELS
        and isinstance(node.func.value, ast.Name)
        and "log" in node.func.value.id.lower()
    ]


def leaky_log_calls(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{path.relative_to(SRC.parent)}:{call.lineno}"
        for call in operator_log_calls(tree)
        if any(leaky_value(value) for value in message_values(call))
    ]


def test_no_error_log_formats_an_exception_or_url_into_its_message():
    """The operator filter keeps the message line and cuts a traceback to its
    type, so exception text and URLs go to logger.exception's traceback, never
    the message."""
    leaks = [leak for path in sorted(SRC.rglob("*.py")) for leak in leaky_log_calls(path)]

    assert leaks == []


@pytest.mark.parametrize(
    "source, leaks",
    [
        ('logger.error(f"failed: {e}")', True),
        ('logger.critical(f"failed: {str(exc)}")', True),
        ('logger.exception(f"fetching {feed.url} failed")', True),
        ('logger.error("failed: %s", err)', True),
        ('log.error(f"bad {rss_url}")', True),
        ('logger.exception(f"failed for item {item.id}")', False),
        ('logger.warning(f"failed: {e}")', False),
        ('logger.error("failed for account %s", account_id)', False),
    ],
)
def test_the_guard_recognises_leaky_log_calls(source, leaks):
    tree = ast.parse(source)

    found = [call for call in operator_log_calls(tree) if any(leaky_value(v) for v in message_values(call))]

    assert bool(found) is leaks
