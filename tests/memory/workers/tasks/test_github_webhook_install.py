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

HOOK_PATH = re.compile(r"/repos/([^/]+)/([^/]+)/hooks(?:/(\d+))?$")


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
    """GitHub's hooks API, for the repos it knows; any other repo answers `denied`."""

    def __init__(self, hooks: dict[str, list[dict]], denied: int = 404):
        self.hooks = hooks
        self.denied = denied
        self.calls: list[tuple[str, str, dict | None]] = []
        self.next_id = 1000

    def request(self, method: str, url: str, json: dict | None = None, **_) -> requests.Response:
        method = method.upper()
        self.calls.append((method, url, json))
        if url.endswith("/app/hook/config"):
            return response(200, json)
        if url.endswith("/access_tokens"):
            return response(201, {"token": "ghs_installation"})
        owner, name, hook_id = HOOK_PATH.search(url).groups()  # type: ignore[union-attr]
        hooks = self.hooks.get(f"{owner}/{name}")
        if hooks is None:
            return response(self.denied, {"message": "Not Found"})
        if method == "GET":
            return response(200, hooks)
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
        return [(m, HOOK_PATH.search(u).group(0)) for m, u, _ in self.calls if m != "GET"]  # type: ignore[union-attr]


@pytest.fixture
def serve():
    patches = []

    def start(hooks: dict[str, list[dict]], denied: int = 404) -> FakeGithub:
        fake = FakeGithub(hooks, denied)
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


def add_repo(db_session, account, name: str, active: bool = True) -> GithubRepo:
    repo = GithubRepo(account_id=account.id, owner="org", name=name, active=active)
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
    fake = serve({"org/a": []})

    github.ensure_github_webhooks(account.id)

    (hook,) = fake.hooks["org/a"]
    assert hook["config"] == {
        "url": webhook_url(account.id), "content_type": "json", "secret": "s3cret", "insecure_ssl": "0"
    }
    assert (hook["events"], hook["active"]) == (HOOK_EVENTS, True)


def test_patches_our_hook_and_leaves_others_alone(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"org/a": [ours(account, events=["issues"], active=False), dict(FOREIGN)]})

    github.ensure_github_webhooks(account.id)

    assert fake.writes() == [("PATCH", "/repos/org/a/hooks/7")]
    mine, other = fake.hooks["org/a"]
    assert (mine["events"], mine["active"], mine["config"]["secret"]) == (HOOK_EVENTS, True, "s3cret")
    assert other == FOREIGN


def test_a_rotated_secret_is_patched_onto_the_existing_hook(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"org/a": [ours(account)]})
    github.ensure_github_webhooks(account.id)

    account.webhook_secret = "rotated"
    db_session.commit()
    github.ensure_github_webhooks(account.id)

    assert fake.hooks["org/a"][0]["config"]["secret"] == "rotated"
    assert len(fake.hooks["org/a"]) == 1


@pytest.mark.parametrize("denied, status", [(403, "missing_permission"), (404, "missing_permission"), (422, "rejected"), (500, "error")])
def test_a_failing_repo_records_why_and_the_rest_still_install(db_session, serve, account, denied, status):
    blocked = add_repo(db_session, account, "blocked")
    fine = add_repo(db_session, account, "fine")
    fake = serve({"org/fine": []}, denied=denied)

    github.ensure_github_webhooks(account.id)

    db_session.refresh(blocked)
    db_session.refresh(fine)
    assert (blocked.webhook_status, fine.webhook_status) == (status, "installed")
    assert blocked.webhook_checked_at is not None
    assert len(fake.hooks["org/fine"]) == 1


def test_inactive_repos_are_skipped(db_session, serve, account):
    add_repo(db_session, account, "paused", active=False)
    fake = serve({"org/paused": []})

    github.ensure_github_webhooks(account.id)

    assert fake.calls == []


def test_removal_deletes_only_our_hooks(db_session, serve, account):
    repo = add_repo(db_session, account, "a")
    repo.webhook_status = "installed"
    account.webhook_secret = None
    db_session.commit()
    fake = serve({"org/a": [ours(account), dict(FOREIGN)]})

    github.remove_github_webhooks(account.id)

    assert fake.hooks["org/a"] == [FOREIGN]
    db_session.refresh(repo)
    assert repo.webhook_status is None


def test_removal_keeps_hooks_once_a_new_secret_exists(db_session, serve, account):
    add_repo(db_session, account, "a")
    fake = serve({"org/a": [ours(account)]})

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


def test_an_app_account_sets_the_app_hook_instead_of_repo_hooks(db_session, serve, app_account):
    repo = add_repo(db_session, app_account, "a")
    fake = serve({"org/a": []})

    github.ensure_github_webhooks(app_account.id)

    (config,) = [body for m, u, body in fake.calls if u.endswith("/app/hook/config")]
    assert config == {"url": webhook_url(app_account.id), "content_type": "json", "secret": "s3cret", "insecure_ssl": "0"}
    assert [u for _, u, _ in fake.calls if "/repos/" in u] == []
    db_session.refresh(repo)
    assert repo.webhook_status == "app_hook"


def test_the_daily_pass_queues_every_active_account_with_a_secret(db_session, owner, account):
    make_account(db_session, owner, active=False)
    no_secret = make_account(db_session, owner)
    no_secret.webhook_secret = None
    db_session.commit()

    with patch.object(github.app, "send_task") as send_task:
        github.ensure_all_github_webhooks()

    assert [c.kwargs["args"] for c in send_task.call_args_list] == [[account.id]]
