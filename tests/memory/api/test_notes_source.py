"""The Notes tab's endpoints: the vault's sync status, and its admin-only git setup."""

import json
import subprocess
from unittest.mock import patch

import pytest
import redis

from memory.common import instance_settings, settings
from memory.common.celery_app import SETUP_GIT_NOTES, TRACK_GIT_CHANGES
from memory.common.db.models import InstanceSetting, Note, PendingJob
from memory.common.notes_git import GIT_SYNC_STATUS_KEY

SETUP = {"remote_url": "https://git.example.com/me/notes.git", "author_name": "Me", "author_email": "me@example.com"}


@pytest.fixture(autouse=True)
def no_env_token(monkeypatch):
    monkeypatch.setattr(settings, "NOTES_GIT_TOKEN", "")
    monkeypatch.setattr(settings, "SSH_PUBLIC_KEY_FILE", settings.FILE_STORAGE_DIR / "ssh_public_key")


@pytest.fixture
def sent():
    """The tasks the endpoints queued, as (name, kwargs)."""
    with patch("memory.common.jobs.celery_app.send_task") as send_task:
        send_task.return_value.id = "task-1"
        yield lambda: [(c.args[0], c.kwargs["kwargs"]) for c in send_task.call_args_list]


@pytest.fixture
def member_client(client, user, db_session):
    """The client, signed in as a real non-admin user: access checks need real project roles."""
    user.scopes = ["read", "write"]
    db_session.flush()
    return client


def git(*args: str):
    subprocess.run(["git", "-C", str(settings.NOTES_STORAGE_DIR), *args], check=True, capture_output=True)


def existing_vault(remote: str | None):
    git("init", "-q", "-b", "master")
    git("config", "user.name", "Old Name")
    git("config", "user.email", "old@example.com")
    if remote:
        git("remote", "add", "origin", remote)


def test_members_see_the_count_and_last_sync_but_not_the_git_setup(member_client, user, db_session):
    db_session.add(Note(sha256=b"n1", modality="text", filename="notes/n1.md", creator_id=user.id))
    db_session.commit()
    status = {"ok": False, "failed_step": "push", "hint": "Pushing failed", "at": "2026-10-25T10:00:00+00:00"}
    redis.from_url(settings.REDIS_URL).set(GIT_SYNC_STATUS_KEY, json.dumps(status))

    response = member_client.get("/notes-source")

    assert response.status_code == 200
    assert response.json() == {"count": 1, "last_sync": status, "git": None}


def test_admins_see_an_unset_vault(client):
    assert client.get("/notes-source").json() == {
        "count": 0,
        "last_sync": None,
        "git": {
            "has_repo": False,
            "branch": None,
            "remote_url": None,
            "remote_locked": False,
            "author_name": None,
            "author_email": None,
            "token": {"set": False, "hint": None, "locked": False},
            "ssh_public_key": None,
        },
    }


def test_an_existing_remote_is_shown_without_its_password_and_locked(client):
    existing_vault("https://me:hunter2@git.example.com/me/notes.git")
    (settings.FILE_STORAGE_DIR / "ssh_public_key").write_text("ssh-ed25519 AAAA memory\n")

    git_view = client.get("/notes-source").json()["git"]

    assert git_view | {"token": None} == {
        "has_repo": True,
        "branch": "master",
        "remote_url": "https://me:***@git.example.com/me/notes.git",
        "remote_locked": True,
        "author_name": "Old Name",
        "author_email": "old@example.com",
        "token": None,
        "ssh_public_key": "ssh-ed25519 AAAA memory",
    }


def test_a_sole_remote_not_named_origin_is_shown_and_locked(client):
    existing_vault(None)
    git("remote", "add", "backup", "git@github.com:me/notes.git")

    git_view = client.get("/notes-source").json()["git"]

    assert (git_view["remote_url"], git_view["remote_locked"]) == ("git@github.com:me/notes.git", True)


def test_a_vault_config_that_isnt_utf8_still_loads(client):
    existing_vault(None)
    with (settings.NOTES_STORAGE_DIR / ".git" / "config").open("ab") as config:
        config.write(b"[user]\n\tname = \xff\xfe\n")

    assert client.get("/notes-source").status_code == 200


def test_setup_stores_the_token_write_only_and_queues_the_setup(client, db_session, sent):
    response = client.put("/notes-source/git", json=SETUP | {"token": "ghp_secret1234"})

    assert response.status_code == 200
    assert response.json()["git"]["token"] == {"set": True, "hint": "1234", "locked": False}
    assert "ghp_secret" not in response.text
    row = db_session.get(InstanceSetting, "notes_git_token")
    assert row is not None and b"ghp_secret" not in row.value_encrypted
    instance_settings.invalidate_cache()
    assert instance_settings.value(instance_settings.NOTES_GIT_TOKEN) == "ghp_secret1234"
    kwargs = {"origin": SETUP["remote_url"], "email": "me@example.com", "name": "Me"}
    assert [(name, {k: v for k, v in kw.items() if k != "job_id"}) for name, kw in sent()] == [
        (SETUP_GIT_NOTES, kwargs)
    ]
    job = db_session.get(PendingJob, response.json()["job_id"])
    assert "ghp_secret" not in json.dumps(job.params)


def test_setup_keeps_an_existing_remote_and_only_updates_the_author(client, sent):
    existing_vault("git@github.com:me/notes.git")

    response = client.put("/notes-source/git", json=SETUP | {"remote_url": "git@github.com:me/notes.git"})

    assert response.status_code == 200
    assert [kw["origin"] for _, kw in sent()] == [None]


def test_setup_refuses_to_repoint_an_existing_remote(client, db_session, sent):
    existing_vault("git@github.com:me/notes.git")

    response = client.put("/notes-source/git", json=SETUP | {"token": "ghp_secret1234"})

    assert response.status_code == 409
    assert response.json()["detail"][0]["loc"] == ["body", "remote_url"]
    assert sent() == []
    assert db_session.get(InstanceSetting, "notes_git_token") is None


@pytest.mark.parametrize(
    "remote_url",
    [
        "",
        "/srv/notes.git",
        "file:///srv/notes.git",
        "ext::sh -c touch% /tmp/pwned",
        "http://git.example.com/me/notes.git",
        "https://me:pw@git.example.com/me/notes.git",
        "ssh://-oProxyCommand=evil/notes.git",
        "-u@host:notes.git",
        "https://git.example.com/",
    ],
)
def test_setup_rejects_remotes_that_arent_plain_https_or_ssh(client, sent, remote_url):
    response = client.put("/notes-source/git", json=SETUP | {"remote_url": remote_url})

    assert response.status_code == 400
    assert response.json()["detail"][0]["loc"] == ["body", "remote_url"]
    assert sent() == []


@pytest.mark.parametrize(
    "field, value", [("author_name", " "), ("author_email", "nope"), ("author_email", "a@b.c\nx")]
)
def test_setup_rejects_a_bad_author(client, sent, field, value):
    response = client.put("/notes-source/git", json=SETUP | {field: value})

    assert response.status_code == 400
    assert response.json()["detail"][0]["loc"] == ["body", field]


def test_a_token_set_in_the_env_cant_be_replaced(client, monkeypatch, sent):
    monkeypatch.setattr(settings, "NOTES_GIT_TOKEN", "env-token-9876")

    response = client.put("/notes-source/git", json=SETUP | {"token": "ghp_secret1234"})

    assert response.status_code == 409
    assert response.json()["detail"][0]["loc"] == ["body", "token"]
    assert client.get("/notes-source").json()["git"]["token"] == {"set": True, "hint": "9876", "locked": True}


def test_the_token_can_be_cleared(client, db_session, sent):
    client.put("/notes-source/git", json=SETUP | {"token": "ghp_secret1234"})

    response = client.delete("/notes-source/git/token")

    assert response.status_code == 200
    assert response.json()["git"]["token"] == {"set": False, "hint": None, "locked": False}
    assert db_session.get(InstanceSetting, "notes_git_token") is None


def test_sync_now_queues_a_sync(client, sent):
    existing_vault("git@github.com:me/notes.git")

    response = client.post("/notes-source/sync")

    assert response.status_code == 200
    assert [name for name, _ in sent()] == [TRACK_GIT_CHANGES]


def test_sync_now_needs_a_repo(client, sent):
    assert client.post("/notes-source/sync").status_code == 409
    assert sent() == []


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("put", "/notes-source/git", SETUP),
        ("delete", "/notes-source/git/token", None),
        ("post", "/notes-source/sync", None),
    ],
)
def test_git_setup_is_admin_only(regular_client, sent, method, path, body):
    assert regular_client.request(method, path, json=body).status_code == 403
    assert sent() == []


@pytest.mark.parametrize("token", ["ghp_ab cd", "ghp_ab\ncd", "ghp_ab\x00cd"])
def test_setup_rejects_a_token_with_whitespace_or_control_characters(client, db_session, sent, token):
    response = client.put("/notes-source/git", json=SETUP | {"token": token})

    assert response.status_code == 400
    assert response.json()["detail"][0]["loc"] == ["body", "token"]
    assert db_session.get(InstanceSetting, "notes_git_token") is None


def test_sync_now_refuses_while_a_sync_is_queued(client, sent):
    existing_vault("git@github.com:me/notes.git")

    first = client.post("/notes-source/sync")
    second = client.post("/notes-source/sync")

    assert (first.status_code, second.status_code) == (200, 409)
    assert len(sent()) == 1


@pytest.mark.parametrize("path", ["/notes-source", "/source-tabs"])
def test_a_redis_outage_hides_the_last_sync_rather_than_failing(client, path):
    with patch("memory.api.notes_source.last_sync", side_effect=redis.ConnectionError):
        assert client.get(path).status_code == 200


def test_confirmed_destination_change_is_queued(client, sent):
    existing_vault("git@github.com:me/notes.git")
    response = client.put("/notes-source/git", json=SETUP | {
        "branch": "notes", "confirm_destination_change": True,
    })
    assert response.status_code == 200
    assert sent()[0][1]["origin"] == SETUP["remote_url"]
    assert sent()[0][1]["branch"] == "notes"
    assert sent()[0][1]["change_destination"] is True


@pytest.mark.parametrize("branch", ["-bad", "a..b", "bad name", "a.lock", "a//b", "@{1}", "a\\b"])
def test_invalid_branch_is_rejected(client, sent, branch):
    response = client.put("/notes-source/git", json=SETUP | {"branch": branch})
    assert response.status_code == 400
    assert sent() == []


def test_destination_change_clears_the_old_saved_token(client, db_session, sent):
    client.put("/notes-source/git", json=SETUP | {"token": "old-secret"})
    existing_vault("git@github.com:me/notes.git")
    response = client.put("/notes-source/git", json=SETUP | {"confirm_destination_change": True, "token": " "})
    assert response.status_code == 200
    assert response.json()["git"]["token"]["set"] is False


def test_branch_change_requires_confirmation(client, sent):
    existing_vault("git@github.com:me/notes.git")
    response = client.put("/notes-source/git", json=SETUP | {"remote_url": None, "branch": "new-destination"})
    assert response.status_code == 409
    assert sent() == []
