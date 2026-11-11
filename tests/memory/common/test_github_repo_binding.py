"""Who may bind a tracked GitHub repo to a project, and where its sync files items.

A repo is tracked once, by one account; its sync files every new or changed
issue and PR under the repo's project, which grants that project's teams
access. So binding someone else's repo needs GitHub access to it, and the
sync files only into projects its owner shares.
"""

from datetime import datetime, timedelta

import pytest

from memory.agents import raw_events
from memory.api.MCP.servers.projects import upsert
from memory.common.access_control import owner_may_file_into_project
from memory.common.db import connection as db_connection
from memory.common.db.models import APIKey, Event, HumanUser, Person, UserSession
from memory.common.db.models.events import record_raw_event
from memory.common.db.models.sources import GithubAccount, GithubRepo, Project, Team, team_members
from memory.common.github import GithubClient
from memory.workers.tasks import github
from tests.conftest import mcp_auth_context


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


def github_user(db_session, email: str, scopes: list[str]) -> HumanUser:
    user = HumanUser(name=email, email=email, password_hash="x", scopes=scopes)
    db_session.add(user)
    db_session.flush()
    account = GithubAccount(user_id=user.id, name=email, auth_type="pat", active=True)
    account.access_token = "ghp_" + email
    db_session.add_all([account, Person(identifier=email, display_name=email, user_id=user.id)])
    db_session.flush()
    return user


def team_of(db_session, slug: str, *users: HumanUser) -> Team:
    team = Team(name=slug, slug=slug, is_active=True)
    db_session.add(team)
    db_session.flush()
    for user in users:
        person = db_session.query(Person).filter_by(user_id=user.id).one()
        db_session.execute(team_members.insert().values(team_id=team.id, person_id=person.id, role="member"))
    db_session.flush()
    return team


@pytest.fixture
def alice(db_session) -> HumanUser:
    return github_user(db_session, "alice@example.com", ["read", "write", "projects"])


@pytest.fixture
def alice_repo(db_session, alice) -> GithubRepo:
    repo = GithubRepo(account_id=alice.github_accounts[0].id, github_id=1, owner="victim", name="secret", active=True)
    db_session.add(repo)
    db_session.commit()
    return repo


def login(db_session, user: HumanUser) -> str:
    token = f"token-{user.id}"
    db_session.add(UserSession(token=token, user_id=user.id, expires_at=datetime.now() + timedelta(days=1)))
    db_session.commit()
    return token


def github_answers(monkeypatch, info: dict | None) -> list[tuple[str, str]]:
    """Make GitHub answer `info` for any repo; returns the repos it was asked about."""
    asked: list[tuple[str, str]] = []

    def fetch_repository_info(self, owner, repo):
        asked.append((owner, repo))
        return info

    monkeypatch.setattr(GithubClient, "fetch_repository_info", fetch_repository_info)
    return asked


async def bind(db_session, user: HumanUser, repo_arg: str) -> list[int]:
    """Have `user` create a repo project for `repo_arg` with a team of theirs; the projects now bound to a repo."""
    team = team_of(db_session, f"team-{user.id}", user)
    token = login(db_session, user)
    with mcp_auth_context(token):
        await upsert.fn(repo=repo_arg, team_ids=[team.id])
    db_session.expire_all()
    return [p.id for p in db_session.query(Project).filter(Project.repo_id.isnot(None))]


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_arg", ["victim/secret", "VICTIM/Secret", "%/%", "victim/secre_", "_ictim/%"])
async def test_a_user_without_github_access_cannot_bind_someone_elses_repo(
    db_session, monkeypatch, alice_repo, repo_arg
):
    bob = github_user(db_session, "bob@example.com", ["read", "write", "projects"])
    github_answers(monkeypatch, None)

    assert await bind(db_session, bob, repo_arg) == []


@pytest.mark.asyncio
async def test_a_user_whose_github_credentials_read_the_repo_may_bind_it(db_session, monkeypatch, alice_repo):
    bob = github_user(db_session, "bob@example.com", ["read", "write", "projects"])
    asked = github_answers(monkeypatch, {"github_id": 1, "owner": "victim", "name": "secret"})

    bound = await bind(db_session, bob, "victim/secret")

    assert (len(bound), asked) == (1, [("victim", "secret")])
    assert db_session.get(Project, bound[0]).repo_id == alice_repo.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binder, repo_arg",
    [("alice", "victim/secret"), ("alice", "Victim/SECRET"), ("admin", "victim/secret")],
)
async def test_the_tracker_or_an_admin_binds_without_asking_github(
    db_session, monkeypatch, alice, alice_repo, binder, repo_arg
):
    users = {"alice": alice, "admin": github_user(db_session, "root@example.com", ["*"])}
    asked = github_answers(monkeypatch, None)

    bound = await bind(db_session, users[binder], repo_arg)

    assert (len(bound), asked) == (1, [])
    assert db_session.get(Project, bound[0]).repo_id == alice_repo.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key_scopes, bound_count",
    [(["read", "write", "projects"], 0), (["*"], 1)],
    ids=["narrowed key", "admin key"],
)
async def test_an_admins_narrowed_key_binds_only_what_github_lets_it(
    db_session, monkeypatch, alice_repo, key_scopes, bound_count
):
    admin = github_user(db_session, "root@example.com", ["*"])
    team = team_of(db_session, "roots", admin)
    key = APIKey.create(user_id=admin.id, scopes=key_scopes)
    db_session.add(key)
    db_session.commit()
    github_answers(monkeypatch, None)

    with mcp_auth_context(key.key, scopes=key_scopes):
        await upsert.fn(repo="victim/secret", team_ids=[team.id])
    db_session.expire_all()

    assert db_session.query(Project).filter(Project.repo_id == alice_repo.id).count() == bound_count


@pytest.mark.parametrize(
    "owner_scopes, owner_in_team, project_has_team, allowed",
    [
        (["read"], True, True, True),
        (["read"], False, True, False),
        (["read"], False, False, True),
        (["*"], False, True, True),
    ],
    ids=["owner in team", "owner not in team", "project without teams", "admin owner"],
)
def test_owner_may_file_into_project(db_session, owner_scopes, owner_in_team, project_has_team, allowed):
    owner = github_user(db_session, "o@example.com", owner_scopes)
    other = github_user(db_session, "x@example.com", ["read"])
    team = team_of(db_session, "t", *([owner] if owner_in_team else [other]))
    project = Project(id=-5, title="p", state="open", teams=[team] if project_has_team else [])
    db_session.add(project)
    db_session.commit()

    assert owner_may_file_into_project(db_session, owner, project.id) is allowed


def bound_project(db_session, repo: GithubRepo, team: Team, number: int | None = None) -> Project:
    project = Project(
        id=-10 - (number or 0), title="p", state="open", repo_id=repo.id, number=number, teams=[team]
    )
    db_session.add(project)
    db_session.commit()
    return project


@pytest.mark.parametrize("alice_in_team", [True, False])
def test_the_sync_files_into_a_repo_project_only_when_its_owner_shares_it(
    db_session, alice, alice_repo, alice_in_team
):
    bob = github_user(db_session, "bob@example.com", ["read"])
    team = team_of(db_session, "bobs", *([bob, alice] if alice_in_team else [bob]))
    project = bound_project(db_session, alice_repo, team)

    expected = project.id if alice_in_team else None
    assert github._lookup_repo_project_id(db_session, alice_repo) == expected
    assert github._lookup_milestone_id(db_session, alice_repo, None) is None


@pytest.mark.parametrize("alice_in_team", [True, False])
def test_the_sync_files_into_a_milestone_project_only_when_its_owner_shares_it(
    db_session, alice, alice_repo, alice_in_team
):
    bob = github_user(db_session, "bob@example.com", ["read"])
    team = team_of(db_session, "bobs", *([bob, alice] if alice_in_team else [bob]))
    milestone = bound_project(db_session, alice_repo, team, number=3)

    expected = milestone.id if alice_in_team else None
    assert github._lookup_milestone_id(db_session, alice_repo, 3) == expected


@pytest.mark.parametrize("milestone", [None, 3])
@pytest.mark.parametrize("alice_in_team", [True, False])
def test_a_raw_deliverys_container_files_as_the_sync_does(db_session, alice, alice_repo, milestone, alice_in_team):
    bob = github_user(db_session, "bob@example.com", ["read"])
    team = team_of(db_session, "bobs", *([bob, alice] if alice_in_team else [bob]))
    project = bound_project(db_session, alice_repo, team, number=milestone)
    payload = {"issue": {"milestone": {"number": milestone} if milestone else None}}
    record_raw_event(
        db_session, source="github", event_type="issues", account_id=alice_repo.account_id,
        owner_id=alice.id, delivery_id="d1", outcome="no_item_type", container="1", payload=payload,
    )
    db_session.commit()
    [event] = db_session.query(Event).filter_by(kind="raw").all()

    container = raw_events.container_of(db_session, event)

    assert container is not None
    assert container.project_id == (project.id if alice_in_team else None)
