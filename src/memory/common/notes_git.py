"""What the api and the notes worker share about the notes vault's git sync.

The api has no git binary, so it reads the vault's git config as a file.
"""

import configparser
import json
import pathlib
import re
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

import redis

from memory.common import settings

GIT_SYNC_STATUS_KEY = "memory:notes:git_sync_status"

TOKEN_ENV = "MEMORY_NOTES_GIT_TOKEN"
# The token stays in the environment, out of argv, the vault's config and git's output.
# A username in the remote URL is kept; otherwise any name works with a token on GitHub and GitLab.
# printf, not echo: dash's echo expands backslashes in the token.
CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || return 0; u=; while read -r l; do case "$l" in username=*) u=1;; esac; done; '
    "test -n \"$u\" || printf 'username=x-access-token\\n'; "
    f"printf 'password=%s\\n' \"${TOKEN_ENV}\"; }}; f"
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# A host can't start with "-", or ssh would read it as an option.
HOST = r"[A-Za-z0-9][A-Za-z0-9.-]*"
SCP_REMOTE = re.compile(rf"^[A-Za-z0-9_][A-Za-z0-9._-]*@{HOST}:[^\s]+$")
URL_SCHEMES = ("https", "ssh")


def credential_args(host: str) -> list[str]:
    """git -c options that answer only ``host``'s https credential prompt with the token in TOKEN_ENV.

    Any other host (a redirect, a submodule) gets no helper at all, so no token.
    """
    return ["-c", "credential.helper=", "-c", f"credential.https://{host}.helper={CREDENTIAL_HELPER}"]


def https_host(url: str | None) -> str | None:
    """The host (and port) to scope the token to, for an https remote."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        return None
    return f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname


def check_token(token: str) -> str:
    token = token.strip()
    if not token or re.search(r"\s", token) or CONTROL_CHARACTERS.search(token):
        raise ValueError("A token is one word with no spaces or control characters")
    return token


def check_remote_url(url: str) -> str:
    """The remote, stripped; ValueError unless it is an https or ssh remote with no password in it.

    Local paths, file://, ext:: and other transports would let a remote run commands or read the server's files.
    """
    url = url.strip()
    if not url:
        raise ValueError("Enter the remote's URL")
    if CONTROL_CHARACTERS.search(url):
        raise ValueError("The URL can't contain control characters")
    if SCP_REMOTE.match(url):
        return url
    parts = urlsplit(url)
    if parts.scheme not in URL_SCHEMES or not parts.hostname or not re.fullmatch(HOST, parts.hostname):
        raise ValueError("Use an https:// or ssh:// URL, or user@host:path")
    if (parts.username or "").startswith("-"):
        raise ValueError("The URL's user can't start with -")
    if parts.password is not None:
        raise ValueError("Leave the password out of the URL: save it as the access token")
    if not parts.path.strip("/"):
        raise ValueError("The URL needs the repository's path")
    return url


def redact(url: str) -> str:
    """The URL without any password, for showing a remote set outside Memory."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    return parts._replace(netloc=f"{parts.username}:***@{parts.netloc.rsplit('@', 1)[1]}").geturl()


@dataclass(frozen=True)
class VaultGit:
    has_repo: bool
    # False when .git/config can't be parsed, so its remotes are unknown.
    config_readable: bool = True
    remotes: tuple[str, ...] = ()
    branch: str | None = None
    remote_name: str | None = None
    remote_url: str | None = None
    author_name: str | None = None
    author_email: str | None = None


def config_value(config: configparser.ConfigParser, section: str, key: str) -> str | None:
    value = config.get(section, key, fallback=None)
    return value.strip().strip('"') if value else None


def sync_remote(branch_remote: str | None, remotes: tuple[str, ...]) -> str | None:
    """The remote a sync uses: the branch's upstream, else origin, else the vault's only remote."""
    if branch_remote:
        return branch_remote
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else None


def read_text(path: pathlib.Path) -> str:
    # Shown, never written back, so undecodable bytes may as well be replaced.
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def read_vault_git(root: pathlib.Path) -> VaultGit:
    """The vault's branch, the remote it syncs with (as git_tracking picks it) and its commit author."""
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return VaultGit(has_repo=git_dir.exists(), config_readable=not git_dir.exists())
    head = read_text(git_dir / "HEAD").strip()
    branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref: refs/heads/") else None
    config = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        config.read_string(read_text(git_dir / "config"))
    except configparser.Error:
        return VaultGit(has_repo=True, config_readable=False, branch=branch)
    remotes = tuple(
        section.removeprefix('remote "').removesuffix('"')
        for section in config.sections()
        if section.startswith('remote "')
    )
    branch_remote = config_value(config, f'branch "{branch}"', "remote") if branch else None
    remote = sync_remote(branch_remote, remotes)
    url = config_value(config, f'remote "{remote}"', "url") if remote else None
    return VaultGit(
        has_repo=True,
        remotes=remotes,
        branch=branch,
        remote_name=remote,
        remote_url=redact(url) if url else None,
        author_name=config_value(config, "user", "name"),
        author_email=config_value(config, "user", "email"),
    )


def last_sync() -> dict | None:
    """The last sync's {ok, failed_step, hint, at}, or None before the first one."""
    raw = redis.from_url(settings.REDIS_URL).get(GIT_SYNC_STATUS_KEY)
    return json.loads(cast(bytes, raw)) if raw else None


def check_branch(branch: str) -> str:
    """Validate a literal branch name without git (the API image has no git)."""
    if (not branch or branch.startswith("-") or branch == "@" or branch.endswith(".")
        or ".." in branch or "@{" in branch or re.search(r"[\s\x00-\x1f\x7f~^:?*\[\\]", branch)
        or any(not part or part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))):
        raise ValueError("Enter a valid git branch name")
    return branch


def token_for_remote(remote_url: str | None) -> str:
    """Read a fresh token bound to this destination; never use a worker's cached secret."""
    from memory.common import instance_settings
    from memory.common.db.connection import make_session
    from memory.common.db.models import InstanceSetting

    setting = instance_settings.NOTES_GIT_TOKEN
    if token := setting.env_value():
        return token
    with make_session() as db:
        row = db.get(InstanceSetting, setting.key)
        if row is None or not isinstance(row.value, dict) or row.value.get("remote_url") != remote_url:
            return ""
        return instance_settings.decode(setting, row)
