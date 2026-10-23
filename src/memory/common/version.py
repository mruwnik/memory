import json
import logging
import os
import pathlib
import re
import subprocess
import time
from collections.abc import Iterable
from functools import cache

import httpx
from alembic.script import ScriptDirectory
from sqlalchemy import text

from memory.common import settings

logger = logging.getLogger(__name__)

REPO_ROOT = pathlib.Path(__file__).parents[3]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
RELEASE_TAG = re.compile(r"v?(\d+(?:\.\d+)*)")

# (monotonic time checked, tag); failures are cached too so a dead release
# source costs one request per interval, not one per health check.
latest_release_cache: tuple[float, str | None] | None = None


@cache
def dev_version() -> str:
    try:
        return subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "dev"


def app_version() -> str:
    """Release tag or git sha baked in at image build time (APP_VERSION)."""
    return os.getenv("APP_VERSION") or dev_version()


def heads_str(heads: Iterable[str]) -> str | None:
    return ",".join(sorted(heads)) or None


@cache
def expected_revision() -> str | None:
    return heads_str(ScriptDirectory(str(MIGRATIONS_DIR)).get_heads())


def db_revision(conn) -> str | None:
    if not conn.execute(text("SELECT to_regclass('alembic_version')")).scalar():
        return None
    return heads_str(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())


def parse_release(body: str) -> str | None:
    try:
        data = json.loads(body)
    except ValueError:
        data = body.strip()
    else:
        data = data.get("tag_name") if isinstance(data, dict) else None
    return data if isinstance(data, str) and RELEASE_TAG.fullmatch(data) else None


async def latest_release() -> str | None:
    """Latest release tag from LATEST_RELEASE_URL, cached; None if unknown.

    Never raises: a broken release source must not fail /health.
    """
    global latest_release_cache
    url = settings.LATEST_RELEASE_URL
    if not url:
        return None
    if latest_release_cache and (
        time.monotonic() - latest_release_cache[0] < settings.LATEST_RELEASE_CHECK_INTERVAL
    ):
        return latest_release_cache[1]
    tag = None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(url)
            response.raise_for_status()
            tag = parse_release(response.text)
    except Exception as e:
        logger.warning("Latest release check failed: %s", e)
    latest_release_cache = (time.monotonic(), tag)
    return tag


def release_tuple(tag: str) -> tuple[int, ...] | None:
    if not (match := RELEASE_TAG.fullmatch(tag)):
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_update_available(current: str, latest: str | None) -> bool | None:
    """None when either side isn't a plain release tag (e.g. a dev sha)."""
    if latest is None:
        return None
    current_t, latest_t = release_tuple(current), release_tuple(latest)
    if current_t is None or latest_t is None:
        return None
    return latest_t > current_t


async def version_info(db_rev: str | None) -> dict:
    current = app_version()
    latest = await latest_release()
    return {
        "version": current,
        "db_revision": db_rev,
        "expected_revision": expected_revision(),
        "latest_release": latest,
        "update_available": is_update_available(current, latest),
    }
