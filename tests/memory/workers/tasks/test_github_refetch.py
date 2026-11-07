"""Single-item GitHub refetches and the trigger changes a sync reports."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
import requests

from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.db import connection as db_connection
from memory.common.db.models import Event, GithubItem, User
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import (
    GithubComment,
    GithubIssueData,
    GithubPRDataDict,
    GithubReview,
    serialize_issue_data,
)
from memory.common.github.refetch import queue_refetch
from memory.workers.tasks import github
from tests.conftest import item_events

OWN = "memory-bot"


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


def comment(id: int, author: str, at: str) -> GithubComment:
    return GithubComment(id=id, author=author, body=f"comment {id}", created_at=at, updated_at=at)


def review(id: int, user: str) -> GithubReview:
    return GithubReview(
        id=id, user=user, state="commented", body="", submitted_at="2024-01-06T00:00:00Z", commit_oid=None
    )


def pr_data(**overrides) -> GithubIssueData:
    extended = overrides.pop("reviews", [])
    data = GithubIssueData(
        kind="pr",
        number=7,
        title="Add feature",
        body="PR body",
        state="open",
        author="contributor",
        labels=[],
        assignees=[],
        milestone_number=None,
        created_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 6, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
        pr_data=GithubPRDataDict(
            diff=None,
            files=[],
            additions=0,
            deletions=0,
            changed_files_count=0,
            reviews=extended,
            review_comments=[],
        ),
        head_sha="a" * 40,
        checks={"tests": "in_progress"},
    )
    return GithubIssueData(**{**data, **overrides})  # type: ignore[typeddict-item]


LATER = "2024-01-07T00:00:00Z"
EARLIER = "2024-01-05T12:00:00Z"


@pytest.mark.parametrize(
    "changed, expected",
    [
        ({"head_sha": "b" * 40}, ["commits"]),
        ({"comments": [comment(1, "alice", LATER)]}, ["comments"]),
        ({"comments": [comment(1, OWN, LATER)]}, []),
        ({"comments": [comment(1, "alice", EARLIER)]}, []),
        ({"reviews": [review(5, "alice")]}, ["review"]),
        ({"reviews": [review(5, OWN)]}, []),
        ({"checks": {"tests": "success"}}, ["checks"]),
        ({"state": "closed"}, ["state"]),
        ({"merged_at": datetime(2024, 1, 7, tzinfo=timezone.utc)}, ["state"]),
        ({"title": "Renamed"}, ["edited"]),
        ({"body": "New body"}, ["edited"]),
        ({"labels": ["x"]}, []),
        (
            {"head_sha": "b" * 40, "checks": {}, "comments": [comment(1, "bob", LATER)]},
            ["commits", "comments", "checks"],
        ),
    ],
)
def test_item_changes(changed, expected):
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())

    assert github.item_changes(existing, pr_data(**changed), {OWN}) == expected


@pytest.mark.parametrize("field, after", [("head_sha", "b" * 40), ("checks", {"x": "success"})])
def test_item_changes_ignores_a_first_recorded_value(field, after):
    """Items stored before head SHAs and checks were recorded must not all fire on their next sync."""
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())
    setattr(existing, field, None)

    assert github.item_changes(existing, pr_data(**{field: after}), {OWN}) == []


def test_item_changes_new_item():
    assert github.item_changes(None, pr_data(), {OWN}) == ["new"]


@pytest.mark.parametrize("own", ["memory-app[bot]", "memory-app"])
def test_item_changes_matches_app_bot_logins_with_or_without_suffix(own):
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())

    new = pr_data(comments=[comment(1, "memory-app", LATER)], reviews=[review(5, "memory-app[bot]")])
    assert github.item_changes(existing, new, {own}) == []


# --- Refetch task against a real DB, GitHub mocked at its HTTP boundary ---


@pytest.fixture
def account(db_session):
    user = User(name="Owner", email="gh-owner@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    account = GithubAccount(
        user_id=user.id, name="acct", auth_type="pat", access_token="ghp_x", verified_login=OWN, active=True
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def repo(db_session, account):
    repo = GithubRepo(
        account_id=account.id,
        github_id=555,
        owner="testorg",
        name="testrepo",
        track_issues=True,
        track_prs=True,
        track_project_fields=False,
        labels_filter=[],
        tags=[],
        active=True,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


def pr_node(head: str, updated: str = "2024-01-06T00:00:00Z", comments=(), checks=None) -> dict:
    contexts = [{"name": n, "status": "COMPLETED", "conclusion": c.upper()} for n, c in (checks or {}).items()]
    return {
        "number": 7,
        "title": "Add feature",
        "body": "PR body",
        "state": "OPEN",
        "author": {"login": "contributor"},
        "labels": {"nodes": []},
        "assignees": {"nodes": []},
        "milestone": None,
        "createdAt": "2024-01-05T00:00:00Z",
        "updatedAt": updated,
        "closedAt": None,
        "mergedAt": None,
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
        "headRefOid": head,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]},
        "comments": {
            "nodes": [
                {"databaseId": i, "author": {"login": a}, "body": f"c{i}", "createdAt": at, "updatedAt": at}
                for i, (a, at) in enumerate(comments, 1)
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "reviews": {"nodes": []},
        "reviewThreads": {"nodes": []},
        "files": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
    }


def http_response(json_body=None, text="") -> Mock:
    response = Mock(ok=True, status_code=200, text=text, headers={"X-RateLimit-Remaining": "4999"})
    response.json.return_value = json_body or {}
    return response


@pytest.fixture
def github_serves():
    """Serve the given PR node to GraphQL; return the GraphQL POST mock."""
    patches = []

    def serve(node: dict) -> Mock:
        for p in patches:
            p.stop()
        patches.clear()
        post = patch.object(
            requests.Session, "post", return_value=http_response({"data": {"repository": {"pullRequest": node}}})
        )
        get = patch.object(requests.Session, "get", return_value=http_response(text="diff"))
        patches.extend([post, get])
        get.start()
        return post.start()

    yield serve
    for p in patches:
        p.stop()


def forget_events(db):
    db.query(Event).delete()
    db.commit()


def changes_reported(db):
    return [(item_id, changes) for item_id, _, changes in item_events(db)]


def stored(db_session) -> GithubItem:
    db_session.expire_all()
    return db_session.query(GithubItem).filter_by(repo_path="testorg/testrepo", number=7).one()


def test_refetch_after_synchronize_reports_commits(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    queue_refetch(repo.id, 7, "pr")
    post = github_serves(pr_node("b" * 40, updated="2024-01-07T00:00:00Z"))
    result = github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert post.call_count == 1
    assert result["status"] == "processed"
    assert item.head_sha == "b" * 40
    assert changes_reported(db_session) == [(item.id, ["commits"])]
    assert queue_refetch(repo.id, 7, "pr")  # the run cleared its debounce mark


def test_refetch_ignores_own_comment(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    github_serves(pr_node("a" * 40, updated=LATER, comments=[(OWN, LATER)]))
    github.refetch_github_item(repo.id, 7, "pr")

    assert stored(db_session).comment_count == 1
    assert changes_reported(db_session) == []


def test_refetch_reports_checks_without_reembedding(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40, checks={"tests": "failure"}))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    github_serves(pr_node("a" * 40, checks={"tests": "success"}))
    result = github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert result["status"] == "updated"
    assert item.checks == {"tests": "success"}
    assert changes_reported(db_session) == [(item.id, ["checks"])]


def branch_context(base: str, head_repo: str, review_commits=()) -> dict:
    reviews = [
        {"databaseId": i, "author": {"login": "bob"}, "state": "COMMENTED", "body": "", "submittedAt": LATER, "commit": {"oid": oid}}
        for i, oid in enumerate(review_commits, 1)
    ]
    return {
        "baseRefOid": base,
        "headRefName": "feature",
        "headRepository": {"nameWithOwner": head_repo},
        "reviews": {"nodes": reviews},
    }


def test_refetch_stores_branch_context_and_reviewed_commits(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40) | branch_context("base1", "fork/testrepo"))
    github.refetch_github_item(repo.id, 7, "pr")
    first = stored(db_session)
    assert (first.base_sha, first.head_ref, first.head_repo) == ("base1", "feature", "fork/testrepo")

    github_serves(pr_node("b" * 40, updated=LATER) | branch_context("base2", "testorg/testrepo", ["a" * 40]))
    github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert item.pr_data is not None
    assert (item.base_sha, item.head_repo) == ("base2", "testorg/testrepo")
    assert [r["commit_oid"] for r in item.pr_data.reviews or []] == ["a" * 40]


def test_refetch_forgets_the_head_repo_of_a_deleted_fork(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40) | branch_context("base1", "fork/testrepo"))
    github.refetch_github_item(repo.id, 7, "pr")

    github_serves(pr_node("a" * 40, updated=LATER) | branch_context("base1", "fork/testrepo") | {"headRepository": None})
    github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert (item.base_sha, item.head_ref, item.head_repo) == ("base1", "feature", None)


def test_refetch_skips_untracked_kind(db_session, repo, github_serves):
    repo.track_prs = False
    db_session.commit()
    post = github_serves(pr_node("a" * 40))

    result = github.refetch_github_item(repo.id, 7, "pr")

    assert result["status"] == "skipped"
    assert post.call_count == 0
    assert changes_reported(db_session) == []


# --- Polls that find what the webhook should have delivered ---

def ago(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


@pytest.fixture
def hooked_repo(db_session, repo):
    repo.webhook_status = "installed"
    repo.webhook_checked_at = ago(120)
    db_session.commit()
    return repo


def poll(repo, **changed):
    return github.sync_github_item(repo.id, serialize_issue_data(pr_data(**changed)), [OWN])


def notifications(no_celery_dispatch) -> list:
    return [c for c in no_celery_dispatch.call_args_list if c.args and c.args[0] == SEND_NOTIFICATION]


def test_poll_finding_an_unpushed_change_marks_the_repo_and_notifies_once(
    db_session, hooked_repo, qdrant, no_celery_dispatch
):
    github.sync_item(hooked_repo.id, pr_data(github_updated_at=ago(90)), {OWN})  # pushed

    poll(hooked_repo, title="Renamed", github_updated_at=ago(30))
    db_session.refresh(hooked_repo)
    first_miss = hooked_repo.push_last_miss_at
    assert hooked_repo.push_missing_since == first_miss is not None
    [sent] = notifications(no_celery_dispatch)
    assert "testorg/testrepo" in sent.kwargs["args"][2]
    assert "Renamed" not in sent.kwargs["args"][2]

    poll(hooked_repo, state="closed", github_updated_at=ago(20))
    db_session.refresh(hooked_repo)
    assert hooked_repo.push_missing_since == first_miss
    assert hooked_repo.push_last_miss_at > first_miss
    assert len(notifications(no_celery_dispatch)) == 1


def test_poll_finding_an_unpushed_new_item_marks_the_repo(db_session, hooked_repo, qdrant):
    poll(hooked_repo, github_updated_at=ago(30))

    db_session.refresh(hooked_repo)
    assert hooked_repo.push_missing_since is not None


@pytest.mark.parametrize(
    "repo_fields, updated_minutes_ago",
    [
        ({}, 5),  # its event may still be on its way
        ({}, 150),  # before the hook was installed
        ({"webhook_status": None}, 30),  # no push configured
        ({"webhook_status": "missing_permission"}, 30),
    ],
)
def test_poll_changes_push_cannot_have_missed_leave_the_repo_unmarked(
    db_session, hooked_repo, qdrant, no_celery_dispatch, repo_fields, updated_minutes_ago
):
    for field, value in repo_fields.items():
        setattr(hooked_repo, field, value)
    db_session.commit()

    poll(hooked_repo, github_updated_at=ago(updated_minutes_ago))

    db_session.refresh(hooked_repo)
    assert hooked_repo.push_missing_since is None
    assert notifications(no_celery_dispatch) == []


@pytest.mark.parametrize(
    "polled, changed",
    [
        (False, {"title": "Renamed"}),  # a webhook refetch is push delivering
        (True, {"checks": {"tests": "success"}}),  # checks don't date themselves
        (True, {}),  # nothing new
    ],
)
def test_syncs_that_show_no_missed_push_leave_the_repo_unmarked(
    db_session, hooked_repo, qdrant, polled, changed
):
    github.sync_item(hooked_repo.id, pr_data(github_updated_at=ago(90)), {OWN})

    data = pr_data(github_updated_at=ago(30), **changed)
    github.sync_item(hooked_repo.id, data, {OWN}, polled=polled)

    db_session.refresh(hooked_repo)
    assert hooked_repo.push_missing_since is None
