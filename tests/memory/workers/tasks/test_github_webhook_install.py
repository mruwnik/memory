"""Installing, refreshing and removing an account's repo webhooks on GitHub."""

import json
import re
from unittest.mock import patch

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from memory.common.db import connection as db_connection
from memory.common.db.models import User
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import HOOK_EVENTS, webhook_url
from memory.workers.tasks import github

HOOK_PATH = re.compile(r"/((?:repos/[^/]+|orgs)/[^/]+)/hooks(?:/(\d+))?$")
USER_PATH = re.compile(r"/(users/([^/]+))$")


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


def response(status: int, body=None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.url = "https://api.github.com/"
    r._content = json.dumps(body).encode() if body is not None else b""
    return r


class FakeGithub:
    """GitHub's hooks API, keyed by "repos/<owner>/<name>" or "orgs/<org>", and /users/<login>.
    A login is an Organization when it is in `orgs` (by default: has an "orgs/" key in
    `hooks` or `broken`), else a User. An unknown org answers 404, any other unknown scope
    `denied`; writes to a `read_only` scope answer `refused`, and a `broken` one 500."""

    def __init__(
        self, hooks: dict[str, list[dict]], denied: int = 404, read_only: frozenset[str] = frozenset(),
        refused: int = 403, broken: frozenset[str] = frozenset(), orgs: frozenset[str] | None = None,
    ):
        self.hooks = hooks
        self.orgs = orgs if orgs is not None else {k.removeprefix("orgs/") for k in {*hooks, *broken} if k.startswith("orgs/")}
        self.denied = denied
        self.read_only = read_only
        self.refused = refused
        self.broken = broken
        self.calls: list[tuple[str, str, dict | None]] = []
        self.next_id = 1000

    def request(self, method: str, url: str, json: dict | None = None, **_) -> requests.Response:
        method = method.upper()
        self.calls.append((method, url, json))
        if url.endswith("/access_tokens"):
            return response(201, {"token": "ghs_installation"})
        if user := USER_PATH.search(url):
            scope, login = user.groups()
            if scope in self.broken:
                return response(500, {"message": "Server Error"})
            return response(200, {"login": login, "type": "Organization" if login in self.orgs else "User"})
        scope, hook_id = HOOK_PATH.search(url).groups()  # type: ignore[union-attr]
        if scope in self.broken:
            return response(500, {"message": "Server Error"})
        hooks = self.hooks.get(scope)
        if hooks is None:
            return response(404 if scope.startswith("orgs/") else self.denied, {"message": "Not Found"})
        if method == "GET":
            return response(200, hooks)
        if scope in self.read_only:
            return response(self.refused, {"message": "Resource not accessible by integration"})
        if method == "POST":
            self.next_id += 1
            hooks.append({"id": self.next_id, **(json or {})})
            return response(201, hooks[-1])
        hook = next(h for h in hooks if h["id"] == int(hook_id))
        if method == "PATCH":
            hook.update(json or {})
            return response(200, hook)
        hooks.remove(hook)
        return response(204)

    def writes(self) -> list[tuple[str, str]]:
        return [(m, match.group(0)) for m, u, _ in self.calls if m != "GET" and (match := HOOK_PATH.search(u))]


@pytest.fixture
def serve():
    patches = []

    def start(
        hooks: dict[str, list[dict]], denied: int = 404, read_only: frozenset[str] = frozenset(),
        refused: int = 403, broken: frozenset[str] = frozenset(), orgs: frozenset[str] | None = None,
    ) -> FakeGithub:
        fake = FakeGithub(hooks, denied, read_only, refused, broken, orgs)
        p = patch.object(requests.Session, "request", lambda _self, method, url, **kw: fake.request(method, url, **kw))
        p.start()
        patches.append(p)
        return fake

    yield start
    for p in patches:
        p.stop()


@pytest.fixture
def owner(db_session):
    user = User(name="Owner", email="gh-hooks@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    return user


def make_account(db_session, owner, **fields) -> GithubAccount:
    account = GithubAccount(user_id=owner.id, name="acct", **{"auth_type": "pat", "access_token": "ghp_x", **fields})
    account.webhook_secret = "s3cret"
    db_session.add(account)
    db_session.commit()
    return account


def add_repo(db_session, account, name: str, active: bool = True, owner: str = "org") -> GithubRepo:
    repo = GithubRepo(account_id=account.id, owner=owner, name=name, active=active)
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def account(db_session, owner):
    return make_account(db_session, owner)


def ours(account, **overrides) -> dict:
    config = {"url": webhook_url(account.id), "content_type": "json", "insecure_ssl": "0"}
    return {"id": 7, "name": "web", "active": True, "events": HOOK_EVENTS, "config": config, **overrides}


FOREIGN = {"id": 8, "name": "web", "active": True, "events": ["push"], "config": {"url": "https://ci.example.com/hook"}}


def test_creates_a_hook_where_there_is_none(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"repos/org/a": []})

    github.ensure_github_webhooks(account.id)

    (hook,) = fake.hooks["repos/org/a"]
    assert hook["config"] == {
        "url": webhook_url(account.id), "content_type": "json", "secret": "s3cret", "insecure_ssl": "0"
    }
    assert (hook["events"], hook["active"]) == (HOOK_EVENTS, True)


def test_patches_our_hook_and_leaves_others_alone(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"repos/org/a": [ours(account, events=["issues"], active=False), dict(FOREIGN)]})

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == [("PATCH", "/repos/org/a/hooks/7")]
    mine, other = fake.hooks["repos/org/a"]
    assert (mine["events"], mine["active"], mine["config"]["secret"]) == (HOOK_EVENTS, True, "s3cret")
    assert other == FOREIGN


def test_a_rotated_secret_is_patched_onto_the_existing_hook(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"repos/org/a": [ours(account)]})
    github.ensure_github_webhooks(account.id)

    account.webhook_secret = "rotated"
    db_session.commit()
    github.ensure_github_webhooks(account.id)

    assert fake.hooks["repos/org/a"][0]["config"]["secret"] == "rotated"
    assert len(fake.hooks["repos/org/a"]) == 1


@pytest.mark.parametrize("denied, status", [(403, "missing_permission"), (404, "missing_permission"), (422, "rejected"), (500, "error")])
def test_a_failing_repo_records_why_and_the_rest_still_install(db_session, serve, account, denied, status):
    blocked = add_repo(db_session, account, "blocked")
    fine = add_repo(db_session, account, "fine")
    fake = serve({"repos/org/fine": []}, denied=denied)

    github.ensure_github_webhooks(account.id)

    db_session.refresh(blocked)
    db_session.refresh(fine)
    assert (blocked.webhook_status, fine.webhook_status) == (status, "installed")
    assert blocked.webhook_checked_at is not None
    assert len(fake.hooks["repos/org/fine"]) == 1


def test_inactive_repos_are_skipped(db_session, serve, account):
    add_repo(db_session, account, "paused", active=False)
    fake = serve({"repos/org/paused": []})

    github.ensure_github_webhooks(account.id)

    assert fake.calls == []


def test_removal_deletes_only_our_hooks(db_session, serve, account):
    repo = add_repo(db_session, account, "a")
    repo.webhook_status = "installed"
    account.webhook_secret = None
    db_session.commit()
    fake = serve({"repos/org/a": [ours(account), dict(FOREIGN)]})

    github.remove_github_webhooks(account.id)

    assert fake.hooks["repos/org/a"] == [FOREIGN]
    db_session.refresh(repo)
    assert repo.webhook_status is None


def test_removal_keeps_hooks_once_a_new_secret_exists(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"repos/org/a": [ours(account)]})

    github.remove_github_webhooks(account.id)

    assert fake.calls == []


@pytest.fixture
def app_account(db_session, owner):
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    account = make_account(db_session, owner, auth_type="app", app_id=1, installation_id=2)
    account.access_token = None
    account.private_key = pem.decode()
    db_session.commit()
    return account


def statuses(db_session, *repos) -> list[str | None]:
    for repo in repos:
        db_session.refresh(repo)
    return [r.webhook_status for r in repos]


@pytest.mark.parametrize("auth", ["pat", "app"])
def test_an_existing_org_hook_is_updated_and_covers_every_repo(db_session, serve, account, app_account, auth):
    acct = {"pat": account, "app": app_account}[auth]
    repos = [add_repo(db_session, acct, name) for name in ("a", "b")]
    fake = serve({"orgs/org": [ours(acct, events=["issues"], active=False), dict(FOREIGN)], "repos/org/a": [], "repos/org/b": []})

    github.ensure_github_webhooks(acct.id)

    assert fake.writes() == [("PATCH", "/orgs/org/hooks/7")]
    mine, other = fake.hooks["orgs/org"]
    assert (mine["events"], mine["active"], mine["config"]["secret"]) == (HOOK_EVENTS, True, "s3cret")
    assert other == FOREIGN
    assert statuses(db_session, *repos) == ["org_hook", "org_hook"]


def test_one_org_hook_is_created_when_the_org_has_none(db_session, serve, account):
    repos = [add_repo(db_session, account, name) for name in ("a", "b")]
    fake = serve({"orgs/org": [dict(FOREIGN)], "repos/org/a": [], "repos/org/b": []})

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == [("POST", "/orgs/org/hooks")]
    assert fake.hooks["orgs/org"][1]["config"]["url"] == webhook_url(account.id)
    assert statuses(db_session, *repos) == ["org_hook", "org_hook"]


def test_our_repo_hooks_go_once_an_org_hook_covers_them(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"orgs/org": [ours(account)], "repos/org/a": [ours(account, id=9), dict(FOREIGN)]})

    github.ensure_github_webhooks(account.id)

    assert fake.hooks["repos/org/a"] == [FOREIGN]


def test_an_org_that_hides_its_hooks_gets_no_repo_hooks(db_session, serve, account):
    repo = add_repo(db_session, account, "a")
    fake = serve({"repos/org/a": []}, orgs=frozenset({"org"}))

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == []
    assert statuses(db_session, repo) == ["missing_permission"]


def test_a_github_outage_on_the_org_adds_no_repo_hooks(db_session, serve, account):
    repo = add_repo(db_session, account, "a")
    fake = serve({"orgs/org": [ours(account)], "repos/org/a": []}, broken=frozenset({"orgs/org"}))

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == []
    assert statuses(db_session, repo) == ["error"]


@pytest.mark.parametrize("refused, status", [(403, "missing_permission"), (404, "missing_permission"), (422, "rejected"), (500, "error")])
def test_an_org_that_refuses_a_new_hook_gets_no_repo_hooks(db_session, serve, account, refused, status):
    repo = add_repo(db_session, account, "a")
    fake = serve({"orgs/org": [], "repos/org/a": []}, read_only=frozenset({"orgs/org"}), refused=refused)

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == [("POST", "/orgs/org/hooks")]
    assert statuses(db_session, repo) == [status]


def test_a_personal_account_gets_one_hook_per_repo_and_no_org_calls(db_session, serve, account):
    repos = [add_repo(db_session, account, name, owner="me") for name in ("a", "b")]
    fake = serve({"repos/me/a": [], "repos/me/b": []})

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == [("POST", "/repos/me/a/hooks"), ("POST", "/repos/me/b/hooks")]
    assert not any("/orgs/" in u for _, u, _ in fake.calls)
    assert statuses(db_session, *repos) == ["installed", "installed"]


def test_an_unknown_owner_type_installs_nothing(db_session, serve, account):
    repo = add_repo(db_session, account, "a")
    fake = serve({"repos/org/a": []}, broken=frozenset({"users/org"}))

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == []
    assert statuses(db_session, repo) == ["error"]


def test_each_owner_gets_its_own_outcome(db_session, serve, account):
    covered = add_repo(db_session, account, "a")
    down = add_repo(db_session, account, "b", owner="other")
    serve({"orgs/org": [ours(account)], "repos/org/a": []}, broken=frozenset({"orgs/other"}))

    github.ensure_github_webhooks(account.id)

    assert statuses(db_session, covered, down) == ["org_hook", "error"]


def test_our_hook_is_found_despite_a_trailing_slash(db_session, serve, account):
    add_repo(db_session, account, "a")
    slashed = ours(account, config={"url": webhook_url(account.id) + "/"})
    fake = serve({"orgs/org": [slashed], "repos/org/a": []})

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == [("PATCH", "/orgs/org/hooks/7")]


def test_an_org_hook_we_cannot_update_is_reported_not_duplicated(db_session, serve, account):
    repo = add_repo(db_session, account, "a")
    fake = serve({"orgs/org": [ours(account)], "repos/org/a": [ours(account, id=9)]}, read_only=frozenset({"orgs/org"}))

    github.ensure_github_webhooks(account.id)

    assert [h["id"] for h in fake.hooks["repos/org/a"]] == [9]
    assert statuses(db_session, repo) == ["missing_permission"]


def test_an_app_account_never_touches_the_app_webhook(db_session, serve, app_account):
    add_repo(db_session, app_account, "a")
    fake = serve({"repos/org/a": []})

    github.ensure_github_webhooks(app_account.id)

    assert [u for _, u, _ in fake.calls if "/app/" in u and not u.endswith("/access_tokens")] == []
    assert len(fake.hooks["repos/org/a"]) == 1


@pytest.mark.parametrize("auth", ["pat", "app"])
def test_removal_deletes_our_org_hook(db_session, serve, account, app_account, auth):
    acct = {"pat": account, "app": app_account}[auth]
    add_repo(db_session, acct, "a")
    acct.webhook_secret = None
    db_session.commit()
    fake = serve({"orgs/org": [ours(acct), dict(FOREIGN)], "repos/org/a": []})

    github.remove_github_webhooks(acct.id)

    assert fake.hooks["orgs/org"] == [FOREIGN]


def test_removal_still_resets_statuses_when_github_is_unreachable(db_session, app_account):
    repo = add_repo(db_session, app_account, "a")
    repo.webhook_status = "org_hook"
    app_account.webhook_secret = None
    db_session.commit()

    with patch.object(requests, "post", side_effect=requests.ConnectionError()), patch("time.sleep"):
        github.remove_github_webhooks(app_account.id)

    assert statuses(db_session, repo) == [None]


def test_the_daily_pass_queues_every_active_account_with_a_secret(db_session, owner, account):
    make_account(db_session, owner, active=False)
    no_secret = make_account(db_session, owner)
    no_secret.webhook_secret = None
    db_session.commit()

    with patch.object(github.app, "send_task") as send_task:
        github.ensure_all_github_webhooks()

    assert [c.kwargs["args"] for c in send_task.call_args_list] == [[account.id]]
