"""Shared SSH identities stay private to their owners."""

import pathlib
import shlex

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from memory.common.db.models import SSHKey
from memory.common.encryption import decrypt_value
from memory.common.notes_ssh import ssh_command


def create(client, **values):
    response = client.post(
        "/api/ssh-keys", json={"name": "Git identity", "generate": True, **values}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_generate_replace_and_delete(client, db_session):
    result = create(client)
    key = db_session.get(SSHKey, result["id"])
    private = decrypt_value(key.private_key_encrypted)
    assert private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert "private" not in str(result)
    assert result["fingerprint"].startswith("SHA256:")
    assert [k["id"] for k in client.get("/api/ssh-keys").json()] == [key.id]
    replacement = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.BestAvailableEncryption(b"passphrase"),
        )
        .decode()
    )
    response = client.put(
        f"/api/ssh-keys/{key.id}",
        json={
            "name": "Replaced",
            "private_key": replacement,
            "passphrase": "passphrase",
        },
    )
    assert response.status_code == 200
    db_session.refresh(key)
    assert decrypt_value(key.private_key_encrypted) != private
    assert response.json()["public_key"] != result["public_key"]
    assert client.delete(f"/api/ssh-keys/{key.id}").status_code == 200
    assert db_session.get(SSHKey, key.id) is None


def test_ownership_and_instance_permissions(client, user, regular_user, db_session):
    other = SSHKey(
        user_id=regular_user.id,
        name="Other user",
        public_key="public",
        private_key_encrypted=b"ciphertext",
    )
    db_session.add(other)
    db_session.commit()
    instance = create(client, instance=True)
    assert other.id not in [key["id"] for key in client.get("/api/ssh-keys").json()]
    assert client.put(f"/api/ssh-keys/{other.id}", json={"name": "Taken"}).status_code == 404
    assert client.delete(f"/api/ssh-keys/{other.id}").status_code == 404
    assert client.put(
        "/notes-source/git/ssh",
        json={
            "remote_url": "git@example.com:notes.git",
            "host_keys": instance["public_key"],
            "key_id": other.id,
        },
    ).status_code == 422
    user.scopes = ["read", "write"]
    db_session.commit()
    assert client.get("/api/ssh-keys").json() == []
    assert (
        client.post(
            "/api/ssh-keys", json={"name": "Instance", "generate": True, "instance": True}
        ).status_code
        == 403
    )
    assert (
        client.put(f"/api/ssh-keys/{instance['id']}", json={"name": "Taken"}).status_code
        == 404
    )


@pytest.mark.parametrize("instance_owned", [False, True])
def test_notes_binding_and_temporary_identity(client, db_session, instance_owned):
    instance = create(client, instance=instance_owned)
    remote = "ssh://git@example.com:2222/notes.git"
    setup = {
        "remote_url": remote,
        "host_keys": instance["public_key"],
        "key_id": instance["id"],
    }
    response = client.put("/notes-source/git/ssh", json=setup)
    assert response.status_code == 200
    assert response.json()["git"]["ssh_public_key"] == instance["public_key"]
    assert client.delete(f"/api/ssh-keys/{instance['id']}").status_code == 409
    with ssh_command(remote) as command:
        assert command is not None
        args = shlex.split(command)
        path = pathlib.Path(args[args.index("-i") + 1])
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.read_text().startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert path.read_text() == decrypt_value(
            db_session.get(SSHKey, instance["id"]).private_key_encrypted
        )
        assert (
            (path.parent / "known_hosts")
            .read_text()
            .startswith("[example.com]:2222 ssh-ed25519 ")
        )
        assert "StrictHostKeyChecking=yes" in command
        assert "IdentityAgent=none" in command
    assert not path.exists()
    with pytest.raises(ValueError):
        with ssh_command("git@different.example:notes.git"):
            pass
    assert (
        client.put("/notes-source/git/ssh", json={**setup, "key_id": None}).status_code
        == 200
    )
    assert client.delete(f"/api/ssh-keys/{instance['id']}").status_code == 200
    with pytest.raises(ValueError, match="Select a database SSH key"):
        with ssh_command(remote):
            pass


@pytest.mark.parametrize(
    "body",
    [
        {"name": " ", "generate": True},
        {"name": "Bad", "private_key": "not a key"},
        {"name": "Both", "generate": True, "private_key": "secret"},
    ],
)
def test_invalid_import_does_not_echo_material(client, body):
    response = client.post("/api/ssh-keys", json=body)
    assert response.status_code == 422
    if body.get("private_key"):
        assert body["private_key"] not in response.text
