"""Single-item GitHub refetches and the changes a sync reports."""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
import requests
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from memory.agents import subscriptions
from memory.agents.subscriptions import IngestConfig
from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.content_processing import create_content_hash
from memory.common.db import connection as db_connection
from memory.common.db.models import AgentDefinition, Delivery, Event, GithubItem, PendingJob, Subscription, User
from memory.common.db.models.sources import CONTENT_FULL, CONTENT_METADATA, GithubAccount, GithubRepo
from memory.common.github import (
    GithubClient,
    GithubComment,
    GithubFileChange,
    GithubIssueData,
    GithubPRDataDict,
    GithubQueryError,
    GithubReview,
    GithubReviewComment,
    serialize_issue_data,
)
from memory.common.github.refetch import queue_refetch
from memory.workers.tasks import github
from tests.conftest import item_events, route_item

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
        checks_state="PENDING",
        draft=False,
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
        ({"checks": {"tests": "success"}, "checks_state": "SUCCESS"}, ["checks", "checks_complete"]),
        ({"checks": {"tests": "success"}}, ["checks"]),  # the rollup still waits on checks not seen
        ({"checks_state": "EXPECTED"}, []),
        (
            {"head_sha": "b" * 40, "checks": {"tests": "failure"}, "checks_state": "FAILURE"},
            ["commits", "checks", "checks_complete"],
        ),
        ({"state": "closed"}, ["state"]),
        ({"merged_at": datetime(2024, 1, 7, tzinfo=timezone.utc)}, ["state"]),
        ({"title": "Renamed"}, ["edited"]),
        ({"body": "New body"}, ["edited"]),
        ({"labels": ["x"]}, ["labels"]),
        ({"assignees": ["bob"]}, ["assignees"]),
        ({"draft": True}, ["draft"]),
        (
            {"head_sha": "b" * 40, "checks": {}, "comments": [comment(1, "bob", LATER)]},
            ["commits", "comments", "checks"],
        ),
    ],
)
def test_item_changes(changed, expected):
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())

    assert github.item_changes(existing, pr_data(**changed), {OWN}).tags == expected


@pytest.mark.parametrize(
    "field, after", [("head_sha", "b" * 40), ("checks", {"x": "success"}), ("draft", True)]
)
def test_item_changes_ignores_a_first_recorded_value(field, after):
    """Items stored before head SHAs, checks and draft were recorded must not all fire on their next sync."""
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())
    setattr(existing, field, None)

    assert github.item_changes(existing, pr_data(**{field: after}), {OWN}).tags == []


@pytest.mark.parametrize(
    "changed, expected",
    [
        ({"reviews": [review(5, "alice"), review(6, OWN)]}, {"review": ["alice"]}),
        ({"comments": [comment(1, "bob", LATER), comment(2, "bob", EARLIER)]}, {"comments": ["bob"]}),
        ({"head_sha": "b" * 40, "head_author": "dev"}, {"commits": ["dev"]}),
        ({"head_sha": "b" * 40}, {}),
        ({"head_author": "dev"}, {}),
        ({"reviews": [review(5, f"{OWN}[bot]")]}, {}),
    ],
)
def test_item_changes_name_who_made_the_comments_reviews_and_commits(changed, expected):
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())

    assert github.item_changes(existing, pr_data(**changed), {OWN}).actors == expected


def test_item_changes_new_item():
    assert github.item_changes(None, pr_data(), {OWN}) == (["new"], {})


@pytest.mark.parametrize("own", ["memory-app[bot]", "memory-app"])
def test_item_changes_matches_app_bot_logins_with_or_without_suffix(own):
    repo = GithubRepo(id=1, owner="o", name="r", tags=[])
    existing = github._create_github_item(repo, pr_data())

    new = pr_data(comments=[comment(1, "memory-app", LATER)], reviews=[review(5, "memory-app[bot]")])
    assert github.item_changes(existing, new, {own}).tags == []


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


def rollup(state: str | None, contexts: list[dict], author: str | None = None) -> dict:
    commit = {
        "statusCheckRollup": {"state": state, "contexts": {"nodes": contexts}},
        "author": {"user": author and {"login": author}},
    }
    return {"commits": {"nodes": [{"commit": commit}]}}


def pr_node(
    head: str,
    updated: str = "2024-01-06T00:00:00Z",
    comments=(),
    checks=None,
    state=None,
    head_author=None,
) -> dict:
    contexts = [{"context": n, "state": c.upper()} for n, c in (checks or {}).items()]
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
        "isDraft": False,
        **rollup(state, contexts, head_author),
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

    def serve(node: dict | None, errors: list[dict] | None = None) -> Mock:
        for p in patches:
            p.stop()
        patches.clear()
        body = {"data": {"repository": {"pullRequest": node}}, "errors": errors}
        post = patch.object(requests.Session, "post", return_value=http_response(body))
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


@pytest.mark.parametrize(
    "first_updated, later_updated, backfills",
    [
        (timedelta(days=1.9), timedelta(0), [False, False]),
        (timedelta(days=2.1), timedelta(0), [True, False]),
        # a change only seen days later is still a change, not an import
        (timedelta(days=30), timedelta(days=5), [True, False]),
    ],
)
def test_items_last_updated_long_before_they_are_first_stored_are_backfill(
    db_session, repo, qdrant, first_updated, later_updated, backfills
):
    now = datetime.now(timezone.utc)
    github.sync_item(repo.id, pr_data(github_updated_at=now - first_updated), {OWN})
    github.sync_item(repo.id, pr_data(title="Renamed", github_updated_at=now - later_updated), {OWN})

    db_session.expire_all()
    assert [e.backfill for e in db_session.query(Event).filter(Event.kind == "item").order_by(Event.id)] == backfills


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


def test_refetch_records_who_made_each_change(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40) | branch_context("base", "testorg/testrepo"))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    node = pr_node(
        "b" * 40, updated=LATER, comments=[("carol", LATER), (OWN, LATER)], head_author="dev"
    )
    github_serves(node | branch_context("base", "testorg/testrepo", ["b" * 40]))
    github.refetch_github_item(repo.id, 7, "pr")

    [event] = db_session.query(Event).all()
    assert (event.changes, event.actors) == (
        ["commits", "comments", "review"],
        {"commits": ["dev"], "comments": ["carol"], "review": ["bob"]},
    )


def test_refetch_ignores_own_comment(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    github_serves(pr_node("a" * 40, updated=LATER, comments=[(OWN, LATER)]))
    github.refetch_github_item(repo.id, 7, "pr")

    assert stored(db_session).comment_count == 1
    assert changes_reported(db_session) == []


def test_refetch_reports_checks_without_reembedding(db_session, repo, qdrant, github_serves):
    github_serves(pr_node("a" * 40, checks={"tests": "failure"}, state="FAILURE"))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    github_serves(pr_node("a" * 40, checks={"tests": "success"}, state="SUCCESS"))
    result = github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert result["status"] == "updated"
    assert item.checks == {"tests": "success"}
    assert changes_reported(db_session) == [(item.id, ["checks", "checks_complete"])]


def test_checks_complete_fires_when_the_head_rollup_reaches_a_new_result(
    db_session, repo, qdrant, github_serves
):
    github_serves(pr_node("a" * 40, checks={"tests": "pending"}, state="PENDING"))
    github.refetch_github_item(repo.id, 7, "pr")
    steps = [
        ("a", "success", "SUCCESS", ["checks", "checks_complete"]),
        ("a", "pending", "PENDING", ["checks"]),  # a re-run
        ("a", "success", "SUCCESS", ["checks"]),
        ("a", "failure", "FAILURE", ["checks", "checks_complete"]),
        ("b", "success", "PENDING", ["commits", "checks"]),  # one check done, others not started
        ("b", "success", "SUCCESS", ["checks_complete"]),
    ]

    reported = []
    for head, outcome, state, _ in steps:
        forget_events(db_session)
        github_serves(pr_node(head * 40, checks={"tests": outcome}, state=state))
        github.refetch_github_item(repo.id, 7, "pr")
        reported.append([changes for _, changes in changes_reported(db_session)])

    assert reported == [[changes] for *_, changes in steps]


def test_check_runs_finishing_report_checks_complete(db_session, repo, qdrant, github_serves):
    running = {"name": "tests", "status": "IN_PROGRESS", "conclusion": None}
    github_serves(pr_node("a" * 40) | rollup("PENDING", [running]))
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    done = {"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}
    github_serves(pr_node("a" * 40) | rollup("SUCCESS", [done]))
    github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert item.checks == {"tests": "success"}
    assert changes_reported(db_session) == [(item.id, ["checks", "checks_complete"])]


@pytest.mark.parametrize(
    "before, after, change, metadata",
    [
        ({"isDraft": True}, {"isDraft": False}, "draft", (False, [], [], [])),
        ({}, {"labels": {"nodes": [{"name": "bug"}]}}, "labels", (False, ["bug"], [], ["bug"])),
        ({}, {"assignees": {"nodes": [{"login": "bob"}]}}, "assignees", (False, [], ["bob"], [])),
    ],
)
def test_refetch_reports_metadata_changes_once_without_reembedding(
    db_session, repo, qdrant, github_serves, before, after, change, metadata
):
    github_serves(pr_node("a" * 40) | before)
    github.refetch_github_item(repo.id, 7, "pr")
    forget_events(db_session)

    github_serves(pr_node("a" * 40) | after)
    result = github.refetch_github_item(repo.id, 7, "pr")
    first = changes_reported(db_session)
    forget_events(db_session)
    github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert result["status"] == "updated"
    assert first == [(item.id, [change])]
    assert changes_reported(db_session) == []
    assert (item.draft, item.labels, item.assignees, item.tags) == metadata


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


# --- GitHub refusing what was asked for ---

UNREADABLE = "Resource not accessible by integration"
NO_COMMITS = "The access token can't read commits on testorg/testrepo: "
NO_PRS = "The access token can't read pull requests on testorg/testrepo: "


def withheld(*path: str | int) -> dict:
    return {"type": "FORBIDDEN", "path": list(path), "message": UNREADABLE}


def without_commit(node: dict) -> dict:
    return node | {"commits": {"nodes": [None]}}


def job(db, job_type: str) -> tuple[str, str | None]:
    found = db.query(PendingJob).filter_by(job_type=job_type).one()
    return found.status, found.error_message


def test_refetch_keeps_a_pr_whose_head_commit_is_withheld_and_flags_the_repo(
    db_session, repo, qdrant, github_serves
):
    github_serves(without_commit(pr_node("a" * 40)), [withheld("repository", "pullRequest", "commits", "nodes", 0)])

    result = github.refetch_github_item(repo.id, 7, "pr")

    db_session.refresh(repo)
    assert result["status"] == "processed"
    assert (stored(db_session).head_sha, stored(db_session).checks) == ("a" * 40, None)
    assert (repo.sync_error or "").startswith(NO_COMMITS)
    # The next complete sync re-fetches it from there.
    assert repo.incomplete_since == datetime(2024, 1, 6, tzinfo=timezone.utc)
    assert repo.sync_incomplete is True


def test_a_refetch_github_refuses_fails_its_job_and_flags_the_repo(db_session, repo, github_serves):
    repo.sync_incomplete = True
    db_session.commit()
    github_serves(None, [withheld("repository", "pullRequest")])

    with pytest.raises(GithubQueryError):
        github.refetch_github_item(repo.id, 7, "pr")

    db_session.refresh(repo)
    status, error = job(db_session, "refetch_github_item")
    assert (status, (error or "").startswith(NO_PRS)) == ("failed", True)
    assert ((repo.sync_error or "").startswith(NO_PRS), repo.sync_incomplete) == (True, False)


def test_a_refetch_of_an_item_github_no_longer_has_is_not_a_failure(db_session, repo, github_serves):
    github_serves(None)

    result = github.refetch_github_item(repo.id, 7, "pr")

    db_session.refresh(repo)
    assert result["status"] == "not_found"
    assert job(db_session, "refetch_github_item") == ("complete", None)
    assert repo.sync_error is None


def github_repo_answers(prs: dict | None, errors: list[dict] | None = None):
    """Serve a repo with no milestones or issues and this pullRequests page."""
    empty = {"nodes": [], "pageInfo": {"hasNextPage": False}}

    def post(url, json, timeout):
        query = json["query"]
        if "pullRequests(" in query:
            return http_response({"data": {"repository": {"pullRequests": prs}}, "errors": errors})
        field = "issues" if "issues(" in query else "milestones"
        return http_response({"data": {"repository": {field: empty}}})

    return patch.multiple(
        requests.Session, post=Mock(side_effect=post), get=Mock(return_value=http_response({"archived": False}))
    )


def pr_page(*nodes: dict) -> dict:
    return {"nodes": list(nodes), "pageInfo": {"hasNextPage": False}}


def test_a_sync_without_commit_access_stores_the_prs_and_catches_up_once_granted(db_session, repo):
    partial = github_repo_answers(
        pr_page(without_commit(pr_node("a" * 40))), [withheld("repository", "pullRequests", "nodes", 0, "commits", "nodes", 0)]
    )
    with partial, patch.object(github, "sync_github_item"):
        degraded = github.sync_github_repo(repo.id)
    db_session.refresh(repo)
    flagged = (repo.sync_error or "").startswith(NO_COMMITS), repo.last_sync_at is not None

    # The PR was last updated before the cursor: only the catch-up fetches it, and only once.
    with github_repo_answers(pr_page(pr_node("a" * 40))), patch.object(github, "sync_github_item"):
        recovered = github.sync_github_repo(repo.id)
        after = github.sync_github_repo(repo.id)
    db_session.refresh(repo)

    assert (degraded["status"], degraded["prs_synced"]) == ("incomplete", 1)
    assert flagged == (True, True)
    assert (recovered["status"], recovered["prs_synced"], after["prs_synced"]) == ("completed", 1, 0)
    assert (repo.sync_error, repo.incomplete_since) == (None, None)


def test_a_quiet_sync_while_commits_are_still_withheld_does_not_catch_up(db_session, repo):
    partial = github_repo_answers(
        pr_page(without_commit(pr_node("a" * 40))), [withheld("repository", "pullRequests", "nodes", 0, "commits", "nodes", 0)]
    )
    with partial, patch.object(github, "sync_github_item"):
        github.sync_github_repo(repo.id)
        db_session.refresh(repo)
        marked = repo.incomplete_since
        # Nothing changed since: the PR is older than the cursor, but GitHub still sends the latest page.
        quiet = github.sync_github_repo(repo.id)
    db_session.refresh(repo)

    assert (quiet["status"], quiet["prs_synced"]) == ("incomplete", 0)
    assert ((repo.sync_error or "").startswith(NO_COMMITS), repo.incomplete_since) == (True, marked)


def test_a_sync_github_refuses_fails_its_job_and_keeps_its_cursor(db_session, repo):
    with github_repo_answers(None, [withheld("repository", "pullRequests")]), patch.object(github, "sync_github_item"):
        with pytest.raises(GithubQueryError):
            github.sync_github_repo(repo.id)

    db_session.refresh(repo)
    status, error = job(db_session, "sync_github_repo")
    assert (status, (error or "").startswith(NO_PRS)) == ("failed", True)
    assert ((repo.sync_error or "").startswith(NO_PRS), repo.last_sync_at) == (True, None)


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
    db_session, hooked_repo, qdrant, no_celery_dispatch, add_sending_email
):
    add_sending_email(hooked_repo.account.user)
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
        (True, {"checks": {"tests": "success"}, "checks_state": "SUCCESS"}),  # checks don't date themselves
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


# --- Repos that store only metadata ---

SECRET = "secret-text"
UPDATED = datetime(2024, 1, 7, tzinfo=timezone.utc)
DIFF = f"diff --git {SECRET}"
FILES = [GithubFileChange(filename="src/secret_module.py", status="modified", additions=1, deletions=0, patch=SECRET)]


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def review_comment(id: int, user: str) -> GithubReviewComment:
    return GithubReviewComment(
        id=id,
        user=user,
        body=f"{SECRET} on line 3",
        path="src/secret_module.py",
        line=3,
        side="RIGHT",
        diff_hunk=f"@@ -1 +1 @@ {SECRET}",
        created_at=LATER,
    )


def extended(reviews=(), review_comments=(), diff=None, files=()) -> GithubPRDataDict:
    return GithubPRDataDict(
        diff=diff,
        files=list(files),
        additions=1,
        deletions=0,
        changed_files_count=len(files),
        reviews=list(reviews),
        review_comments=list(review_comments),
    )


def text_pr(**overrides) -> GithubIssueData:
    """A PR with text in every field a metadata repo drops."""
    data = pr_data(
        body=f"{SECRET} body",
        comments=[GithubComment(id=1, author="alice", body=f"{SECRET} comment", created_at=EARLIER, updated_at=EARLIER)],
        comment_count=1,
        diff_summary=f"{SECRET} diff summary",
        pr_data=extended(
            reviews=[GithubReview(**{**review(5, "bob"), "body": f"{SECRET} review"})],
            review_comments=[review_comment(9, "bob")],
            diff=DIFF,
            files=FILES,
        ),
    )
    return GithubIssueData(**{**data, **overrides})  # type: ignore[typeddict-item]


def stored_text(item: GithubItem) -> str:
    """Every column of the item and its PR data, as one string."""
    rows = [row for row in (item, item.pr_data) if row is not None]
    return repr([getattr(row, attr.key) for row in rows for attr in inspect(row).mapper.column_attrs])


def all_items(db_session) -> list[GithubItem]:
    db_session.expire_all()
    return db_session.query(GithubItem).filter_by(repo_path="testorg/testrepo").order_by(GithubItem.id).all()


def reported_events(db) -> list[tuple[list[str] | None, dict | None]]:
    db.expire_all()
    return [(e.changes, e.actors) for e in db.query(Event).filter(Event.kind == "item").order_by(Event.id)]


@pytest.fixture
def metadata_repo(db_session, repo):
    repo.content_mode = CONTENT_METADATA
    db_session.commit()
    return repo


def test_repos_store_full_content_by_default(repo):
    assert (repo.content_mode, repo.stores_content) == (CONTENT_FULL, True)


def test_content_mode_is_full_or_metadata(db_session, repo):
    repo.content_mode = "summaries"

    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "second, identity",
    [
        ({"number": 8}, ("pr", 8)),  # another PR
        ({"kind": "issue", "pr_data": None}, ("issue", 7)),  # an issue with the PR's number
    ],
)
def test_metadata_repo_stores_items_without_their_text(db_session, metadata_repo, second, identity):
    github.sync_item(metadata_repo.id, text_pr(), {OWN})
    github.sync_item(metadata_repo.id, text_pr(**second), {OWN})

    items = all_items(db_session)
    assert [(i.kind, i.number) for i in items] == [("pr", 7), identity]
    assert len({bytes(i.sha256) for i in items}) == 2
    assert [
        (i.content, i.size, i.diff_summary, i.content_hash, i.embed_status, list(i.chunks)) for i in items
    ] == [("", 0, None, None, "SKIPPED", [])] * 2
    assert [i.body_hash for i in items] == [sha256_hex(f"{SECRET} body")] * 2
    assert [SECRET in stored_text(i) for i in items] == [False, False]
    assert changes_reported(db_session) == [(items[0].id, ["new"]), (items[1].id, ["new"])]


def test_metadata_repo_keeps_review_and_comment_ids_authors_and_times(db_session, metadata_repo):
    github.sync_item(metadata_repo.id, text_pr(), {OWN})

    [item] = all_items(db_session)
    assert item.pr_data is not None
    assert (item.pr_data.diff, item.pr_data.files, item.comment_count) == (None, [], 1)
    assert item.pr_data.reviews == [{**review(5, "bob"), "body": None}]
    assert item.pr_data.review_comments == [
        {**review_comment(9, "bob"), "body": "", "path": "", "diff_hunk": ""}
    ]


def with_review_comments(*comments: GithubReviewComment) -> dict:
    return {"pr_data": extended(review_comments=comments)}


@pytest.mark.parametrize(
    "changed, reported",
    [
        ({}, []),
        ({"title": "Renamed"}, [(["edited"], None)]),
        ({"body": "New body"}, [(["edited"], None)]),
        ({"head_sha": "b" * 40, "head_author": "dev"}, [(["commits"], {"commits": ["dev"]})]),
        ({"comments": [comment(1, "alice", LATER)], "comment_count": 1}, [(["comments"], {"comments": ["alice"]})]),
        (with_review_comments(review_comment(9, "carol")), [(["comments"], {"comments": ["carol"]})]),
        ({"reviews": [review(5, "alice")]}, [(["review"], {"review": ["alice"]})]),
        ({"state": "closed", "closed_at": UPDATED}, [(["state"], None)]),
        ({"state": "closed", "merged_at": UPDATED}, [(["state"], None)]),
        ({"labels": ["bug"]}, [(["labels"], None)]),
        ({"assignees": ["bob"]}, [(["assignees"], None)]),
        ({"checks": {"tests": "success"}, "checks_state": "SUCCESS"}, [(["checks", "checks_complete"], None)]),
    ],
)
def test_metadata_repo_reports_each_change_once(db_session, metadata_repo, changed, reported):
    github.sync_item(metadata_repo.id, pr_data(), {OWN})
    forget_events(db_session)

    new = pr_data(github_updated_at=UPDATED, **changed)
    github.sync_item(metadata_repo.id, new, {OWN})
    first = reported_events(db_session)
    forget_events(db_session)
    github.sync_item(metadata_repo.id, new, {OWN})

    item = stored(db_session)
    assert first == reported
    assert reported_events(db_session) == []
    assert (item.content, item.title, item.state, item.labels, item.assignees, item.body_hash) == (
        "",
        new["title"],
        new["state"],
        new["labels"],
        new["assignees"],
        sha256_hex(new["body"]),
    )


@pytest.mark.parametrize(
    "stored_fields",
    [
        {},
        {"body_hash": None},  # stored before bodies were hashed
    ],
)
@pytest.mark.parametrize(
    "changed, reported",
    [
        ({"state": "closed", "closed_at": UPDATED}, [["state"]]),
        ({"body": "New body"}, [["edited"]]),
        ({"title": "Renamed", "labels": ["bug"]}, [["edited", "labels"]]),
        ({"reviews": [review(5, "alice")]}, [["review"]]),
        ({}, []),  # the stored body, compared by hash or by content, is not an edit
    ],
)
def test_switching_to_metadata_keeps_stored_content_and_reports_changes(
    db_session, repo, qdrant, stored_fields, changed, reported
):
    commented = {"comments": [comment(1, "alice", EARLIER)], "comment_count": 1}
    github.sync_item(repo.id, pr_data(**commented), {OWN})
    item = stored(db_session)
    for field, value in stored_fields.items():
        setattr(item, field, value)
    repo.content_mode = CONTENT_METADATA
    db_session.commit()
    before = (item.content, bytes(item.sha256), sorted(c.id for c in item.chunks), item.embed_status)
    forget_events(db_session)

    new = pr_data(github_updated_at=UPDATED, **commented, **changed)
    github.sync_item(repo.id, new, {OWN})

    item = stored(db_session)
    assert before[-1] == "STORED"
    assert before[0] == github._build_content(pr_data(**commented))
    assert [changes for _, changes in changes_reported(db_session)] == reported
    assert (item.content, bytes(item.sha256), sorted(c.id for c in item.chunks), item.embed_status) == before
    assert (item.title, item.state, item.closed_at, item.labels, item.github_updated_at, item.body_hash) == (
        new["title"],
        new["state"],
        new["closed_at"],
        new["labels"],
        UPDATED,
        sha256_hex(new["body"]),
    )


def test_switching_back_to_full_builds_and_embeds_content(db_session, metadata_repo, qdrant):
    github.sync_item(metadata_repo.id, text_pr(), {OWN})
    metadata_repo.content_mode = CONTENT_FULL
    db_session.commit()
    forget_events(db_session)

    result = github.sync_item(metadata_repo.id, text_pr(), {OWN})

    item = stored(db_session)
    assert item.pr_data is not None
    assert (result["status"], item.embed_status) == ("processed", "STORED")
    assert item.chunks
    content = github._build_content(text_pr())
    assert (item.content, bytes(item.sha256)) == (content, create_content_hash(content))
    assert (item.diff_summary, item.content_hash) == (f"{SECRET} diff summary", "hash")
    assert (item.pr_data.diff, item.pr_data.files) == (DIFF, FILES)
    assert changes_reported(db_session) == []


@pytest.mark.parametrize(
    "mode, diff_fetches, stored_diff",
    [(CONTENT_FULL, 1, "diff text"), (CONTENT_METADATA, 0, None)],
)
def test_refetch_fetches_the_diff_only_for_repos_that_store_content(
    db_session, repo, qdrant, github_serves, mode, diff_fetches, stored_diff
):
    repo.content_mode = mode
    db_session.commit()
    github_serves(pr_node("a" * 40))

    with patch.object(GithubClient, "fetch_pr_diff", return_value="diff text") as fetch_diff:
        github.refetch_github_item(repo.id, 7, "pr")

    item = stored(db_session)
    assert item.pr_data is not None
    assert fetch_diff.call_count == diff_fetches
    assert item.pr_data.diff == stored_diff


@pytest.fixture
def on_edit_subscription(db_session, account):
    definition = AgentDefinition(
        user_id=account.user_id, name="t", spec={"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}
    )
    db_session.add(definition)
    db_session.commit()
    subscription = Subscription(
        user_id=account.user_id,
        name="repo watcher",
        definition_id=definition.id,
        handler={"agent": definition.id},
        kind="ingest",
        config=IngestConfig.model_validate(
            {"filter": {"repos": ["testorg/testrepo"]}, "on_edit": True}
        ).model_dump(mode="json"),
    )
    db_session.add(subscription)
    db_session.commit()
    return subscription


def deliveries(db, subscription) -> list[list[str] | None]:
    db.expire_all()
    return [d.changes for d in db.query(Delivery).filter_by(subscription_id=subscription.id).order_by(Delivery.created_at)]


def recent(hours_ago: float) -> datetime:
    """A time recent enough that a sync of it is live, not a backfill."""
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def commented(id: int, author: str, hours_ago: float) -> dict:
    return {"comments": [comment(id, author, recent(hours_ago).isoformat())], "comment_count": 1}


@pytest.mark.parametrize("mode", [CONTENT_FULL, CONTENT_METADATA])
@pytest.mark.parametrize(
    "before, changed, delivered",
    [
        (dict, lambda: {"body": "New body"}, [["new"], ["edited"]]),
        (dict, lambda: commented(1, "alice", 1.5), [["new"], ["comments"]]),
        # One comment deleted and another posted: the count stays, updated-at moves.
        (lambda: commented(1, "alice", 3), lambda: commented(2, "bob", 1.5), [["new"], ["comments"]]),
        (dict, lambda: with_review_comments(review_comment(9, "carol")), [["new"], ["comments"]]),
    ],
)
def test_repo_changes_reach_on_edit_subscriptions_once(
    db_session, repo, qdrant, on_edit_subscription, no_celery_dispatch, mode, before, changed, delivered
):
    repo.content_mode = mode
    db_session.commit()
    github.sync_item(repo.id, pr_data(github_updated_at=recent(2), **before()), {OWN})
    subscriptions.route_events()
    new = pr_data(github_updated_at=recent(1), **changed())
    github.sync_item(repo.id, new, {OWN})
    subscriptions.route_events()
    github.sync_item(repo.id, new, {OWN})
    subscriptions.route_events()
    # A reprocess of the item as it stands, which only a changed version lets through.
    route_item(db_session, stored(db_session).id)

    assert deliveries(db_session, on_edit_subscription) == delivered


@pytest.mark.parametrize("mode", [CONTENT_FULL, CONTENT_METADATA])
def test_a_sync_moving_only_updated_at_delivers_nothing(
    db_session, repo, qdrant, on_edit_subscription, no_celery_dispatch, mode
):
    repo.content_mode = mode
    db_session.commit()
    github.sync_item(repo.id, pr_data(github_updated_at=recent(2)), {OWN})
    subscriptions.route_events()
    version = subscriptions.content_version(stored(db_session))
    forget_events(db_session)

    github.sync_item(repo.id, pr_data(github_updated_at=recent(1)), {OWN})
    subscriptions.route_events()

    assert subscriptions.content_version(stored(db_session)) != version
    assert reported_events(db_session) == []
    assert deliveries(db_session, on_edit_subscription) == [["new"]]
