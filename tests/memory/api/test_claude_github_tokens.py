import pytest

from memory.claude_containers.github_tokens import github_token_env
from memory.common.db.models import ClaudeGitHubToken, HumanUser, Secret
from memory.common.db.models.secrets import encrypt_value

PAT = "ghp_abcdefghijklmnop1234"


def test_put_stores_encrypted_and_returns_only_hint(client, db_session, user):
    response = client.put("/claude/github-tokens/clone", json={"token": f"  {PAT}\n"})

    assert response.status_code == 200
    assert PAT not in response.text
    assert response.json()["token_hint"] == "1234"
    row = db_session.query(ClaudeGitHubToken).one()
    assert PAT.encode() not in row.token_encrypted
    assert row.token == PAT


def test_get_lists_hints_never_tokens(client):
    client.put("/claude/github-tokens/clone", json={"token": PAT})
    client.put("/claude/github-tokens/write", json={"token": "ghp_write_987654321098"})

    response = client.get("/claude/github-tokens")

    assert response.status_code == 200
    assert PAT not in response.text
    assert "ghp_write_987654321098" not in response.text
    assert {(t["kind"], t["token_hint"]) for t in response.json()} == {
        ("clone", "1234"),
        ("write", "1098"),
    }


def test_put_replaces_existing_token(client, db_session):
    client.put("/claude/github-tokens/clone", json={"token": "ghp_old_00000000000000"})
    client.put("/claude/github-tokens/clone", json={"token": PAT})

    row = db_session.query(ClaudeGitHubToken).one()
    assert row.token == PAT
    assert row.token_hint == "1234"


def test_delete_removes_token(client, db_session):
    client.put("/claude/github-tokens/write", json={"token": PAT})

    assert client.delete("/claude/github-tokens/write").status_code == 200
    assert db_session.query(ClaudeGitHubToken).count() == 0
    assert client.delete("/claude/github-tokens/write").status_code == 404


@pytest.mark.parametrize(
    "path, body",
    [
        ("/claude/github-tokens/admin", {"token": PAT}),
        ("/claude/github-tokens/clone", {"token": "   "}),
        ("/claude/github-tokens/clone", {"token": "ghp abc"}),
    ],
)
def test_put_rejects_bad_input(client, db_session, path, body):
    assert client.put(path, json=body).status_code == 422
    assert db_session.query(ClaudeGitHubToken).count() == 0


def test_tokens_are_per_user(client, db_session):
    other = HumanUser(name="Other", email="other@example.com", password_hash="x")
    db_session.add(other)
    db_session.flush()
    token = ClaudeGitHubToken(user_id=other.id, kind="clone")
    token.token = PAT
    db_session.add(token)
    db_session.commit()

    assert client.get("/claude/github-tokens").json() == []
    assert client.delete("/claude/github-tokens/clone").status_code == 404


def store(db_session, user_id: int, kind: str, value: str) -> None:
    token = ClaudeGitHubToken(user_id=user_id, kind=kind)
    token.token = value
    db_session.add(token)
    db_session.commit()


def test_spawn_env_uses_stored_tokens_when_write_opted_in(db_session, user):
    store(db_session, user.id, "clone", PAT)
    store(db_session, user.id, "write", "ghp_write_987654321098")

    env = github_token_env(db_session, user.id, {}, use_saved_write_token=True)

    assert env == {"GITHUB_TOKEN": PAT, "GITHUB_TOKEN_WRITE": "ghp_write_987654321098"}


def test_spawn_env_withholds_saved_write_token_by_default(db_session, user):
    store(db_session, user.id, "clone", PAT)
    store(db_session, user.id, "write", "ghp_write_987654321098")

    assert github_token_env(db_session, user.id, {}) == {"GITHUB_TOKEN": PAT}


@pytest.mark.parametrize(
    "token",
    [
        "short",
        "ghp_has space_0123456789",
        "ghp_nul\x00_0123456789012",
        "x" * 256,
    ],
)
def test_put_rejects_malformed_token(client, token):
    assert client.put("/claude/github-tokens/clone", json={"token": token}).status_code == 422


@pytest.mark.parametrize("token", [PAT, "github_pat_" + "A1b2C3d4_" * 8])
def test_put_accepts_github_token_formats(client, token):
    assert client.put("/claude/github-tokens/clone", json={"token": token}).status_code == 200


def test_spawn_env_without_tokens_is_empty(db_session, user):
    assert github_token_env(db_session, user.id, {}) == {}


def test_spawn_env_request_secret_name_overrides_stored(db_session, user):
    store(db_session, user.id, "clone", PAT)
    store(db_session, user.id, "write", "ghp_write_987654321098")
    db_session.add(
        Secret(user_id=user.id, name="ci-pat", encrypted_value=encrypt_value("ghp_ci_5555555555555555"))
    )
    db_session.commit()

    env = github_token_env(
        db_session, user.id, {"clone": "ci-pat", "write": None}, use_saved_write_token=True
    )

    assert env == {"GITHUB_TOKEN": "ghp_ci_5555555555555555", "GITHUB_TOKEN_WRITE": "ghp_write_987654321098"}
