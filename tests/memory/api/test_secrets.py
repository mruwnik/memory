"""Tests for the /secrets API.

Pins the security-critical invariants:
- ``GET /secrets`` returns metadata only — never the decrypted value.
- ``GET /secrets/{id}`` likewise omits ``value``; only the explicit
  ``GET /secrets/{id}/value`` route returns the plaintext.
- One user cannot list, read, update, or delete another user's secret;
  ``get_user_secret`` 404s rather than 403s to avoid leaking existence.
- ``validate_symbol_name`` rejects invalid identifiers at write time
  with a 4xx (FastAPI raises 422 for ValueError inside a validator).
- An update with ``value=null`` does NOT erase the stored value.
- The encrypted column is round-trippable via the decrypted ``value``
  property, so a regression that bypasses Fernet would be caught.
"""

import pytest

from memory.common.db.models import HumanUser
from memory.common.db.models.secrets import Secret


@pytest.fixture
def other_user(db_session):
    other = HumanUser(
        id=999,
        email="other@example.com",
        name="Other User",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()
    return other


def _make_secret(db_session, *, user_id: int, name: str, value: str) -> Secret:
    """Insert a secret directly via the ORM (bypasses the API)."""
    secret = Secret(user_id=user_id, name=name)
    secret.value = value  # triggers Fernet encryption
    db_session.add(secret)
    db_session.commit()
    db_session.refresh(secret)
    return secret


# --- create + retrieve round-trip -----------------------------------------


def test_create_then_get_value_round_trips(client, db_session, user):
    """Create -> GET value should round-trip the plaintext exactly."""
    response = client.post(
        "/secrets",
        json={"name": "github-token", "value": "ghp_secret_xyz", "description": "CI token"},
    )

    assert response.status_code == 200
    metadata = response.json()
    secret_id = metadata["id"]
    assert metadata["name"] == "github-token"
    # SecretResponse must NOT carry the value.
    assert "value" not in metadata
    assert "encrypted_value" not in metadata

    value_resp = client.get(f"/secrets/{secret_id}/value")
    assert value_resp.status_code == 200
    body = value_resp.json()
    assert body["value"] == "ghp_secret_xyz"
    assert body["name"] == "github-token"


# --- list response excludes value -----------------------------------------


def test_list_secrets_never_leaks_values(client, db_session, user):
    """GET /secrets returns metadata only; assert no row carries `value`."""
    _make_secret(db_session, user_id=user.id, name="alpha", value="A-PLAIN")
    _make_secret(db_session, user_id=user.id, name="beta", value="B-PLAIN")

    response = client.get("/secrets")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    # Spot-check structure but more importantly: no plaintext, no ciphertext.
    for row in rows:
        assert "value" not in row, "Listing must not surface decrypted values"
        assert "encrypted_value" not in row
    # Ensure the plaintext we wrote is not anywhere in the response body.
    raw = response.text
    assert "A-PLAIN" not in raw
    assert "B-PLAIN" not in raw


def test_get_secret_metadata_excludes_value(client, db_session, user):
    """GET /secrets/{id} (without /value) must not return the decrypted value."""
    secret = _make_secret(
        db_session, user_id=user.id, name="lone-secret", value="meta-only-please"
    )

    response = client.get(f"/secrets/{secret.id}")

    assert response.status_code == 200
    body = response.json()
    assert "value" not in body
    assert "meta-only-please" not in response.text


# --- cross-user access ----------------------------------------------------
#
# regular_client authenticates as `user` (id=1). Secrets created for
# `other_user` (id=999) must be invisible / immutable to that caller.


def test_list_secrets_does_not_show_other_users_secrets(
    regular_client, db_session, other_user
):
    _make_secret(
        db_session, user_id=other_user.id, name="theirs", value="other-plaintext"
    )

    response = regular_client.get("/secrets")

    assert response.status_code == 200
    assert response.json() == []


def test_get_secret_404_for_other_users_secret(
    regular_client, db_session, other_user
):
    """Cross-user reads must 404 (not 403) so existence isn't leaked."""
    secret = _make_secret(
        db_session, user_id=other_user.id, name="theirs", value="X"
    )

    response = regular_client.get(f"/secrets/{secret.id}")

    assert response.status_code == 404
    # Must not echo the secret's name in the error body.
    assert "theirs" not in response.text


def test_get_secret_value_404_for_other_users_secret(
    regular_client, db_session, other_user
):
    """Same 404 stance for the explicit /value route — the high-value path."""
    secret = _make_secret(
        db_session, user_id=other_user.id, name="theirs", value="X"
    )

    response = regular_client.get(f"/secrets/{secret.id}/value")

    assert response.status_code == 404
    assert "X" not in response.text


def test_update_secret_404_for_other_users_secret(
    regular_client, db_session, other_user
):
    secret = _make_secret(
        db_session, user_id=other_user.id, name="theirs", value="orig"
    )

    response = regular_client.patch(
        f"/secrets/{secret.id}", json={"value": "hacked"}
    )

    assert response.status_code == 404
    db_session.expire_all()
    refreshed = db_session.get(Secret, secret.id)
    assert refreshed.value == "orig"


def test_delete_secret_404_for_other_users_secret(
    regular_client, db_session, other_user
):
    secret = _make_secret(
        db_session, user_id=other_user.id, name="theirs", value="X"
    )

    response = regular_client.delete(f"/secrets/{secret.id}")

    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.get(Secret, secret.id) is not None


def test_get_secret_by_name_404_for_other_users_secret(
    regular_client, db_session, other_user
):
    """The by-name route filters by user_id; cross-user lookup must 404."""
    _make_secret(
        db_session, user_id=other_user.id, name="theirs-by-name", value="X"
    )

    response = regular_client.get("/secrets/by-name/theirs-by-name")

    assert response.status_code == 404


# --- input validation -----------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "has space",
        "1starts-with-digit",
        "has@symbol",
        "has.dot",
        "",
    ],
)
def test_create_secret_rejects_invalid_symbol_name(client, db_session, user, bad_name):
    """Pydantic validator → 422 with no row written."""
    response = client.post(
        "/secrets",
        json={"name": bad_name, "value": "anything"},
    )

    assert response.status_code == 422
    # Defense-in-depth: validate_symbol_name is enforced at the model layer too.
    assert (
        db_session.query(Secret).filter(Secret.name == bad_name).count() == 0
    )


def test_duplicate_secret_name_rejected(client, db_session, user):
    """Per-user uniqueness is enforced at the API layer with a 400."""
    client.post("/secrets", json={"name": "dupe", "value": "v1"}).raise_for_status()

    response = client.post("/secrets", json={"name": "dupe", "value": "v2"})

    assert response.status_code == 400


def test_update_with_null_value_does_not_erase(client, db_session, user):
    """SecretUpdate.value=None means 'no change' — must not blank the secret."""
    secret = _make_secret(
        db_session, user_id=user.id, name="keepme", value="original"
    )

    response = client.patch(
        f"/secrets/{secret.id}",
        json={"description": "new description"},
    )

    assert response.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(Secret, secret.id)
    assert refreshed.value == "original"
    assert refreshed.description == "new description"


def test_update_value_re_encrypts_at_rest(client, db_session, user):
    """A successful PATCH should rewrite the encrypted column (not store plaintext)."""
    secret = _make_secret(
        db_session, user_id=user.id, name="rotate", value="v1"
    )
    original_ciphertext = bytes(secret.encrypted_value)

    response = client.patch(f"/secrets/{secret.id}", json={"value": "v2"})

    assert response.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(Secret, secret.id)
    assert refreshed.value == "v2"
    assert refreshed.encrypted_value != original_ciphertext
    # The plaintext should never live in the encrypted column.
    assert b"v2" not in refreshed.encrypted_value


def test_delete_secret_removes_row(client, db_session, user):
    secret = _make_secret(
        db_session, user_id=user.id, name="goner", value="X"
    )

    response = client.delete(f"/secrets/{secret.id}")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    db_session.expire_all()
    assert db_session.get(Secret, secret.id) is None
