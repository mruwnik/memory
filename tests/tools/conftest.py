import os
import subprocess
import time

import pytest

GIT_ENV = {
    "PATH": os.environ["PATH"],
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def keygen(path):
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def keys(tmp_path):
    """(release key, stranger key); only the release key is in `allowed`."""
    return keygen(tmp_path / "release"), keygen(tmp_path / "stranger")


@pytest.fixture
def allowed(tmp_path, keys):
    path = tmp_path / "allowed_signers"
    path.write_text(f"* {keys[0].with_suffix('.pub').read_text()}")
    return path


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def signed_tag(keys, state_dir):
    """Tag HEAD with a tagger date `age_hours` ago and record it as first seen
    `seen` hours ago (default: the same). who: "good" and "stranger" sign with
    that key, "lightweight" makes a lightweight tag, None an unsigned annotated one.
    seen=None never records it, so it counts as first seen when selection runs."""

    def make(repo, name, age_hours, who=None, seen=-1):
        key = {"good": keys[0], "stranger": keys[1]}.get(who)
        kind = ["-s"] if key else [] if who == "lightweight" else ["-a", "-m", name]
        if key:
            kind += ["-m", name]
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}",
             "tag", *kind, name],
            cwd=repo,
            env={**GIT_ENV, "GIT_COMMITTER_DATE": f"{int(time.time()) - age_hours * 3600} +0000"},
            check=True,
            capture_output=True,
        )  # fmt: skip
        seen_hours = age_hours if seen == -1 else seen
        if seen_hours is None:
            return
        sha = subprocess.run(
            ["git", "rev-parse", f"refs/tags/{name}"],
            cwd=repo, env=GIT_ENV, check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip
        state_dir.mkdir(exist_ok=True)
        with (state_dir / "seen-tags").open("a") as f:
            f.write(f"{sha} {int(time.time()) - seen_hours * 3600}\n")

    return make


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "local_stack: needs the local compose stack up; needs --run-slow"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip = pytest.mark.skip(reason="need --run-slow and a running local compose stack")
    for item in items:
        if "local_stack" in item.keywords:
            item.add_marker(skip)
