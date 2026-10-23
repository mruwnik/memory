import subprocess

import httpx
import pytest

from memory.common import version


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    monkeypatch.setattr(version, "latest_release_cache", None)
    version.dev_version.cache_clear()
    yield
    version.dev_version.cache_clear()


def test_app_version_prefers_baked_in_env(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    assert version.app_version() == "v1.2.3"


def test_app_version_falls_back_to_git_describe(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=version.REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert sha in version.app_version()


def test_app_version_is_dev_outside_a_git_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr(version, "REPO_ROOT", tmp_path)
    assert version.app_version() == "dev"


def test_db_is_migrated_to_expected_head(db_session):
    assert version.db_revision(db_session) == version.expected_revision()


@pytest.mark.parametrize(
    "body, expected",
    [
        ("v1.2.3\n", "v1.2.3"),
        ("1.2.3", "1.2.3"),
        ('{"tag_name": "v1.2.3", "name": "Release"}', "v1.2.3"),
        ('{"tag_name": 5}', None),
        ('{"name": "no tag"}', None),
        ('"v1.2.3"', None),
        ("<html><body>v1.2.3</body></html>", None),
        ("[]", None),
        ("", None),
    ],
)
def test_parse_release(body, expected):
    assert version.parse_release(body) == expected


@pytest.mark.parametrize(
    "current, latest, expected",
    [
        ("v1.0.0", "v1.1.0", True),
        ("v1.1.0", "v1.1.0", False),
        ("v2.0.0", "v1.10.0", False),
        ("1.9.0", "v1.10.0", True),
        ("abc1234", "v1.1.0", None),
        ("v1.0.0-3-gabc1234", "v1.1.0", None),
        ("v1.0.0", None, None),
    ],
)
def test_is_update_available(current, latest, expected):
    assert version.is_update_available(current, latest) is expected


def count_fetches(monkeypatch, response=None, error=None):
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append(url)
        if error:
            raise error
        return httpx.Response(200, text=response, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(version.settings, "LATEST_RELEASE_URL", "https://example.test/latest")
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, error, expected",
    [
        ("v1.4.0", None, "v1.4.0"),
        (None, httpx.ConnectError("refused"), None),
        (None, httpx.InvalidURL("bad url"), None),
        (None, RuntimeError("anything"), None),
    ],
)
async def test_latest_release_is_cached_within_interval(monkeypatch, response, error, expected):
    calls = count_fetches(monkeypatch, response, error)
    assert await version.latest_release() == expected
    assert await version.latest_release() == expected
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_latest_release_refetches_after_interval(monkeypatch):
    calls = count_fetches(monkeypatch, "v1.4.0")
    monkeypatch.setattr(version.settings, "LATEST_RELEASE_CHECK_INTERVAL", 0)
    await version.latest_release()
    await version.latest_release()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_latest_release_is_none_when_unconfigured(monkeypatch):
    calls = count_fetches(monkeypatch, "v1.4.0")
    monkeypatch.setattr(version.settings, "LATEST_RELEASE_URL", "")
    assert await version.latest_release() is None
    assert calls == []
