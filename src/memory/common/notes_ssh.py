"""Encrypted, repository-bound SSH credentials for the Notes vault."""

import contextlib
import asyncio
import base64
import hashlib
import pathlib
import os
import shlex
import tempfile
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from sqlalchemy import text
from sqlalchemy.orm import Session

from memory.common.db.connection import make_session
from memory.common.db.models.ssh_keys import SSHKey, NotesSSHConfig
from memory.common.db.models import User
from memory.common.ssh_keys import can_use_key
from memory.common.db.models.secrets import decrypt_value
from memory.common.notes_git import check_remote_url
from memory.common.safe_http import default_policy, resolve, StackAddresses


async def probe_host_keys(remote: str) -> dict:
    """Fetch untrusted host keys without authenticating or changing saved trust."""
    remote = check_remote_url(remote)
    ssh_host(remote)
    parts = urlsplit(remote if remote.startswith("ssh://") else "ssh://" + remote.replace(":", "/", 1))
    host, port = parts.hostname, parts.port or 22
    if not host:
        raise ValueError("Enter an SSH hostname")
    policy = default_policy()
    async with asyncio.timeout(10):
        addresses = await resolve(host)
        stack = await StackAddresses(resolve).get(policy.blocked_hosts)
        policy.check(host, addresses, stack)
        # Dial only a validated numeric address; never let keyscan resolve again.
        process = await asyncio.create_subprocess_exec(
            "ssh-keyscan", "-T", "5", "-p", str(port), "-t", "ed25519,ecdsa,rsa",
            str(addresses[0]), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    if len(output) > 65536:
        raise ValueError("Server returned too many host keys")
    keys = []
    for line in output.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 3:
            raise ValueError("Invalid server host key")
        public = validate_host_keys(" ".join(fields[1:]))
        fingerprint = "SHA256:" + base64.b64encode(
            hashlib.sha256(base64.b64decode(fields[2], validate=True)).digest()
        ).decode().rstrip("=")
        if public not in [key["public_key"] for key in keys]:
            keys.append({"public_key": public, "fingerprint": fingerprint, "algorithm": fields[1]})
    if not keys:
        raise ValueError("No SSH host keys received. Check the hostname and port.")
    return {"remote_url": remote, "host": host, "port": port, "keys": keys}


def ssh_host(remote: str) -> str:
    remote = check_remote_url(remote)
    if remote.startswith("https://"):
        raise ValueError("Enter an SSH remote URL")
    parts = urlsplit(
        remote
        if remote.startswith("ssh://")
        else "ssh://" + remote.replace(":", "/", 1)
    )
    host = parts.hostname
    if not host:
        raise ValueError("Enter an SSH hostname")
    return f"[{host}]:{parts.port}" if parts.port and parts.port != 22 else host


def validate_host_keys(value: str) -> str:
    """Accept public keys only, never user-supplied known_hosts directives or hosts."""
    keys = []
    for line in value.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2 or len(line) > 16384:
            raise ValueError(
                "Enter each host public key as its key type and base64 value, without a hostname or comment"
            )
        serialization.load_ssh_public_key(line.strip().encode())
        keys.append(" ".join(parts))
    return "\n".join(keys)


def ssh_view(db: Session) -> dict | None:
    config = db.get(NotesSSHConfig, 1)
    if config is None:
        return None
    key = db.get(SSHKey, config.ssh_key_id)
    return {
        "key_id": config.ssh_key_id,
        "public_key": key.public_key if key else "",
        "remote_url": config.remote_url,
        "host_keys": config.host_keys,
    }


def configure_ssh(db: Session, remote: str, host_keys: str, key_id: int | None, user: User) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(740193821)"))
    config = db.get(NotesSSHConfig, 1)
    if key_id is None:
        if config:
            db.delete(config)
        db.commit()
        return
    remote = check_remote_url(remote)
    ssh_host(remote)
    host_keys = validate_host_keys(host_keys)
    if not host_keys:
        raise ValueError("Provide at least one trusted server host key")
    key = db.query(SSHKey).filter(SSHKey.id == key_id).with_for_update().first()
    if key is None or not can_use_key(key, user):
        raise ValueError("Choose an accessible SSH key")
    if config is None:
        config = NotesSSHConfig(id=1)
        db.add(config)
    config.ssh_key_id = key_id
    config.remote_url = remote
    config.host_keys = host_keys
    db.commit()


@contextlib.contextmanager
def ssh_command(remote: str | None):
    """Materialize a private key only for one git subprocess, then remove it."""
    if not remote:
        yield None
        return
    is_ssh = (
        remote.startswith("ssh://")
        or ("://" not in remote and ":" in remote.split("/", 1)[0])
    )
    if not is_ssh:
        yield None
        return
    with make_session() as db:
        config = db.get(NotesSSHConfig, 1)
        if config is None:
            raise ValueError("Select a database SSH key and trusted host keys for Notes")
        else:
            if config.remote_url != remote or not config.host_keys:
                raise ValueError(
                    "Configure SSH credentials and trusted host keys for this Notes remote"
                )
            key = db.get(SSHKey, config.ssh_key_id)
            if key is None:
                raise ValueError("Notes SSH key is not available")
            credential = (decrypt_value(key.private_key_encrypted), config.host_keys)
    private, host_keys = credential
    with tempfile.TemporaryDirectory(prefix="memory-notes-ssh-") as directory:
        key = pathlib.Path(directory) / "identity"
        hosts = pathlib.Path(directory) / "known_hosts"
        with open(
            key, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
        ) as file:
            file.write(private)
        hosts.write_text(
            "".join(f"{ssh_host(remote)} {line}\n" for line in host_keys.splitlines())
        )
        yield (
            f"ssh -F /dev/null -i {shlex.quote(str(key))} "
            f"-o UserKnownHostsFile={shlex.quote(str(hosts))} -o GlobalKnownHostsFile=/dev/null "
            "-o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes"
        )
