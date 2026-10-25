import pathlib
import subprocess

import pytest

from tests.tools.conftest import GIT_ENV

REPO_ROOT = pathlib.Path(__file__).parents[2]
GIT_ID = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def checkout_block():
    """bootstrap.sh's Checkout step, rendered with the select_release.sh it embeds."""
    text = (REPO_ROOT / "deploy" / "aws" / "bootstrap.sh").read_text()
    block = text.split('log "Checkout"\n', 1)[1].split("# The code stays root's", 1)[0]
    block = block.split("\ndone\n", 1)[1]
    select_release = (REPO_ROOT / "tools" / "select_release.sh").read_text()
    return block.replace("${select_release}", select_release).replace("$${", "${")


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, env={**GIT_ENV, **GIT_ID}, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path, signed_tag):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "master")
    git(origin, "commit", "-q", "--allow-empty", "-m", "v1.0.0")
    signed_tag(origin, "v1.0.0", 100, "good")
    return origin


@pytest.fixture
def bootstrap_checkout(tmp_path, origin, allowed, state_dir):
    root = tmp_path / "app"
    root.mkdir()
    state_dir.mkdir(exist_ok=True)
    script = "\n".join(
        [
            "set -Eeuo pipefail",
            'log() { echo "==> $*"; }',
            "param() { echo 48; }",
            f"ROOT={root} STATE_DIR={state_dir} SIGNERS={allowed}",
            f"SELECT_RELEASE={tmp_path / 'lib' / 'select_release.sh'} repo_url={origin}",
            checkout_block(),
        ]
    )

    def run():
        result = subprocess.run(
            ["bash", "-c", script], env={**GIT_ENV, **GIT_ID}, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return git(root, "describe", "--tags", "--exact-match")

    return run


@pytest.mark.parametrize(
    "before, expected",
    [
        pytest.param([], "v1.0.0", id="first-install"),
        pytest.param(["checkout"], "v1.0.0", id="retry-already-newest"),
        pytest.param(["checkout", "release"], "v1.1.0", id="retry-picks-fixed-release"),
        pytest.param(["checkout", "release", "installed"], "v1.0.0", id="installed-left-to-updates"),
        pytest.param(["release", "installed"], "v1.0.0", id="installed-lost-checkout-waits-out-delay"),
    ],
)
def test_checkout_step(bootstrap_checkout, origin, signed_tag, state_dir, before, expected):
    def release():
        git(origin, "commit", "-q", "--allow-empty", "-m", "v1.1.0")
        signed_tag(origin, "v1.1.0", 1, "good")

    steps = {
        "checkout": bootstrap_checkout,
        "release": release,
        "installed": (state_dir / "installed").touch,
    }
    for step in before:
        steps[step]()

    assert bootstrap_checkout() == expected
