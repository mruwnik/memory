# pyright: reportFunctionMemberAccess=false
from unittest.mock import Mock, patch

import pytest
import requests

from memory.api.MCP.servers.github import pr_diff
from memory.common.db.models import GithubIssue, GithubPR, HumanUser
from memory.common.db.models.sources import GithubAccount, GithubRepo
from tests.conftest import mcp_auth_context


@pytest.fixture
def repo(db_session):
    owner = HumanUser(name="Owner", email="pr-owner@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    account = GithubAccount(
        user_id=owner.id, name="acct", auth_type="pat", access_token="ghp_owner", active=True
    )
    db_session.add(account)
    db_session.flush()
    repo = GithubRepo(account_id=account.id, owner="testorg", name="testrepo", active=True)
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def items(db_session, repo):
    def item(cls, kind: str, number: int, sensitivity: str = "basic"):
        return cls(
            kind=kind,
            repo_path="testorg/testrepo",
            repo_id=repo.id,
            number=number,
            modality="github",
            sha256=f"pr-diff-{number}".encode(),
            content="body",
            embed_status="STORED",
            sensitivity=sensitivity,
        )

    found = {
        "pr": item(GithubPR, "pr", 7),
        "public_pr": item(GithubPR, "pr", 9, sensitivity="public"),
        "issue": item(GithubIssue, "issue", 8),
    }
    db_session.add_all(found.values())
    db_session.commit()
    return found


@pytest.fixture
def github_get():
    response = Mock(ok=True, status_code=200, text="diff --git a/x b/x", headers={})
    with patch.object(requests.Session, "get", autospec=True, return_value=response) as get:
        yield get


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_fixture, kind, number",
    [("admin_session", "pr", 7), ("user_session", "public_pr", 9)],
)
async def test_pr_diff_fetches_with_the_repo_accounts_credentials(
    request, items, github_get, session_fixture, kind, number
):
    caller = request.getfixturevalue(session_fixture)

    with mcp_auth_context(caller.token):
        diff = await pr_diff.fn(id=items[kind].id)

    session, url = github_get.call_args.args
    assert diff == "diff --git a/x b/x"
    assert url == f"https://api.github.com/repos/testorg/testrepo/pulls/{number}"
    assert github_get.call_args.kwargs["headers"] == {"Accept": "application/vnd.github.diff"}
    assert session.headers["Authorization"] == "Bearer ghp_owner"


def keep(repo):
    pass


def deactivate_account(repo):
    repo.account.active = False


def deactivate_repo(repo):
    repo.active = False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_fixture, kind, change",
    [
        ("user_session", "pr", keep),
        ("admin_session", "issue", keep),
        ("admin_session", "pr", deactivate_account),
        ("admin_session", "pr", deactivate_repo),
    ],
)
async def test_pr_diff_refuses_items_it_cannot_serve(
    request, db_session, repo, items, github_get, session_fixture, kind, change
):
    caller = request.getfixturevalue(session_fixture)
    change(repo)
    db_session.commit()

    with mcp_auth_context(caller.token), pytest.raises(ValueError, match="not found"):
        await pr_diff.fn(id=items[kind].id)

    github_get.assert_not_called()


@pytest.mark.asyncio
async def test_pr_diff_reports_a_failed_fetch(items, admin_session, github_get):
    github_get.return_value = Mock(ok=False, status_code=404, text="", headers={})

    with mcp_auth_context(admin_session.token), pytest.raises(ValueError, match="couldn't fetch"):
        await pr_diff.fn(id=items["pr"].id)


@pytest.mark.asyncio
async def test_pr_diff_survives_a_failed_audit_log(items, admin_session, github_get):
    with mcp_auth_context(admin_session.token), patch(
        "memory.api.MCP.servers.github.log_item_access", side_effect=RuntimeError
    ):
        assert await pr_diff.fn(id=items["pr"].id) == "diff --git a/x b/x"
