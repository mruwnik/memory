import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
ENV = {
    "PATH": os.environ["PATH"],
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def run(cwd, *cmd, env=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        env={**ENV, **(env or {})},
        capture_output=True,
        text=True,
        timeout=60,
    )


def commit(repo, message):
    run(repo, "git", "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    for script in ["select_release.sh", "release.sh"]:
        shutil.copy(REPO_ROOT / "tools" / script, repo / "tools" / script)
    run(repo, "git", "init", "-q", "-b", "master")
    run(repo, "git", "remote", "add", "origin", str(tmp_path / "origin.git"))
    run(tmp_path, "git", "init", "-q", "--bare", str(tmp_path / "origin.git"))
    commit(repo, "init")
    return repo


@pytest.fixture
def select(allowed, state_dir):
    def go(repo, *extra, min_age=48, env=None, installed_here=False):
        if not installed_here:
            # Tags are made on HEAD; selection must not treat that as installed.
            tree = run(repo, "git", "rev-parse", "HEAD^{tree}").stdout.strip()
            elsewhere = run(repo, "git", "commit-tree", tree, "-m", "elsewhere").stdout.strip()
            run(repo, "git", "checkout", "-q", "--detach", elsewhere)
        return run(
            repo,
            "bash", "tools/select_release.sh",
            "--require-signed", str(allowed),
            "--min-age-hours", str(min_age),
            *extra,
            env={"STATE_DIR": str(state_dir), **(env or {})},
        )  # fmt: skip

    return go


@pytest.mark.parametrize(
    "tags, expected",
    [
        pytest.param([("v1.0.0", 100, "good", -1)], "v1.0.0", id="single"),
        pytest.param(
            [("v1.0.0", 300, "good", -1), ("v1.0.9", 200, "good", -1), ("v1.0.10", 100, "good", -1)],
            "v1.0.10",
            id="newest-by-version-not-lexically",
        ),
        pytest.param(
            [("v1.0.0", 100, "good", -1), ("v1.1.0", 1, "good", -1)],
            "v1.0.0",
            id="too-young-skipped-for-older-signed",
        ),
        pytest.param(
            [("v1.0.0", 100, "good", -1), ("v1.1.0", 100, None, -1), ("v1.2.0", 100, "stranger", -1)],
            "v1.0.0",
            id="unsigned-and-unknown-signer-skipped",
        ),
        pytest.param(
            [("v1.0.0", 100, "good", -1), ("v1.1.0", 100, "good", 0)],
            "v1.0.0",
            id="backdated-tag-first-seen-now-is-young",
        ),
        pytest.param(
            [("v1.0.0", 100, "good", -1), ("v1.1.0", 1, "good", 100)],
            "v1.0.0",
            id="old-first-seen-does-not-hide-a-recent-tag-date",
        ),
        pytest.param(
            [("v1.0.0", 100, "good", -1), ("v1.1.0", 100, "lightweight", -1)],
            "v1.0.0",
            id="lightweight-skipped",
        ),
        pytest.param(
            [("v1.0.0", 100, "good", -1), ("v1.1.0-rc1", 100, "good", -1)],
            "v1.0.0",
            id="prerelease-not-auto-selected",
        ),
    ],
)
def test_selects_newest_signed_old_enough_tag(repo, select, signed_tag, tags, expected):
    for name, age, who, seen in tags:
        signed_tag(repo, name, age, who, seen)

    result = select(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "who, age, seen",
    [
        pytest.param(None, 100, -1, id="unsigned"),
        pytest.param("stranger", 100, -1, id="unknown-signer"),
        pytest.param("good", 1, -1, id="too-young"),
        pytest.param("good", 100, 0, id="backdated"),
    ],
)
def test_errors_when_no_tag_qualifies(repo, select, signed_tag, who, age, seen):
    signed_tag(repo, "v1.0.0", age, who, seen)

    result = select(repo)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "no release tag" in result.stderr


def test_errors_with_no_tags(repo, select):
    result = select(repo)

    assert result.returncode != 0
    assert "no release tag" in result.stderr


@pytest.mark.parametrize(
    "who, age, seen, ok, expected",
    [
        ("good", 100, -1, True, "v2.0.0"),
        (None, 100, -1, False, ""),
        ("stranger", 100, -1, False, ""),
        ("lightweight", 100, -1, False, ""),
        ("good", 1, -1, False, ""),
        ("good", 100, 0, False, ""),
        ("good", 100, None, False, ""),
    ],
)
def test_explicit_ref_gets_the_same_checks(repo, select, signed_tag, who, age, seen, ok, expected):
    signed_tag(repo, "v2.0.0", age, who, seen)

    result = select(repo, "v2.0.0")

    assert (result.returncode == 0) is ok, result.stderr
    assert result.stdout.strip() == expected


def test_explicit_older_tag_on_another_branch_is_refused_as_a_downgrade(repo, select, signed_tag):
    signed_tag(repo, "v1.0.0", 100, "good")
    run(repo, "git", "checkout", "-q", "-b", "next")
    commit(repo, "next")
    signed_tag(repo, "v1.1.0", 100, "good")
    run(repo, "git", "checkout", "-q", "-b", "hotfix", "v1.0.0")
    commit(repo, "hotfix")
    signed_tag(repo, "v1.0.1", 100, "good")
    run(repo, "git", "checkout", "-q", "next")

    result = select(repo, "v1.0.1", installed_here=True)

    assert result.returncode != 0
    assert "refusing to downgrade" in result.stderr


def test_explicit_prerelease_tag_is_allowed_after_the_checks(repo, select, signed_tag):
    signed_tag(repo, "v2.0.0-rc1", 100, "good")

    result = select(repo, "v2.0.0-rc1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v2.0.0-rc1"


def test_explicit_backdated_non_v_tag_is_rejected(repo, select, signed_tag, state_dir):
    signed_tag(repo, "release-1", 111, "good", seen=None)

    result = select(repo, "release-1")

    assert result.returncode != 0
    assert "not a v* tag" in result.stderr


def test_explicit_non_tag_ref_is_rejected_when_signatures_required(repo, select):
    result = select(repo, "master")

    assert result.returncode != 0
    assert "not a v* tag" in result.stderr


@pytest.mark.parametrize("explicit", [[], ["v9.9.9"]], ids=["auto", "explicit"])
def test_signed_tag_replayed_under_a_newer_name_is_not_selected(
    repo, select, signed_tag, explicit
):
    signed_tag(repo, "v1.0.0", 100, "good")
    old = run(repo, "git", "rev-parse", "v1.0.0").stdout.strip()
    run(repo, "git", "update-ref", "refs/tags/v9.9.9", old)

    result = select(repo, *explicit)

    assert result.stdout.strip() != "v9.9.9"
    assert (result.returncode == 0) is (not explicit)


def test_pgp_signed_tag_is_not_verified_against_the_keyring(repo, allowed, state_dir):
    # A short path: gpg-agent sockets can't live under pytest's long tmp paths.
    gnupg = tempfile.mkdtemp(dir="/tmp")
    env = {"GNUPGHOME": gnupg}
    run(repo, "gpg", "--batch", "--passphrase", "", "--quick-gen-key", "t@example.com",
        "default", "default", "never", env=env)  # fmt: skip
    tagged = run(
        repo, "git", "-c", "gpg.format=openpgp", "-c", "user.signingkey=t@example.com",
        "tag", "-s", "-m", "v1.0.0", "v1.0.0", env=env,
        )  # fmt: skip
    assert tagged.returncode == 0, tagged.stderr
    keyring_accepts = run(repo, "git", "verify-tag", "v1.0.0", env=env)
    assert keyring_accepts.returncode == 0, keyring_accepts.stderr

    result = run(
        repo,
        "bash", "tools/select_release.sh",
        "--require-signed", str(allowed), "--min-age-hours", "0",
        env={**env, "STATE_DIR": str(state_dir)},
    )  # fmt: skip

    shutil.rmtree(gnupg, ignore_errors=True)
    assert result.returncode != 0
    assert "no release tag" in result.stderr


def test_first_sighting_is_recorded_once(repo, select, signed_tag, state_dir):
    signed_tag(repo, "v1.0.0", 100, "good", seen=None)

    select(repo)
    first = (state_dir / "seen-tags").read_text()
    time.sleep(1.1)
    select(repo)

    assert len(first.splitlines()) == 1
    assert (state_dir / "seen-tags").read_text() == first


@pytest.fixture
def release_repo(repo, tmp_path):
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## v1.2.3\n\n- stuff\n")
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "name": "release-test",
                "version": "1.0.0",
                "scripts": {"build": "node build.cjs"},
            }
        )
    )
    (frontend / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "release-test",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {"": {"name": "release-test", "version": "1.0.0"}},
            }
        )
    )
    (frontend / "build.cjs").write_text("require('node:fs').readFileSync('page.js');\n")
    (frontend / "page.js").write_text("// Application source\n")
    run(repo, "git", "add", ".")
    commit(repo, "changelog")
    run(repo, "git", "push", "-q", "origin", "master")
    return repo


@pytest.fixture
def release(keys):
    def go(repo, version="v1.2.3", test_cmd="true", *flags):
        return run(
            repo,
            "bash", "tools/release.sh", *flags, version,
            env={"RELEASE_TEST_CMD": test_cmd, "SIGNING_KEY": str(keys[0])},
        )  # fmt: skip

    return go


def test_release_creates_signed_tag_that_selection_accepts_and_does_not_push(
    release_repo, release, select, tmp_path
):
    result = release(release_repo)

    assert result.returncode == 0, result.stderr
    assert run(tmp_path, "git", "-C", str(tmp_path / "origin.git"), "tag").stdout == ""
    assert select(release_repo, min_age=0).stdout.strip() == "v1.2.3"


def test_release_push_publishes_master_and_the_tag(release_repo, release, tmp_path):
    result = release(release_repo, "v1.2.3", "true", "--push")

    assert result.returncode == 0, result.stderr
    assert run(tmp_path, "git", "-C", str(tmp_path / "origin.git"), "tag").stdout.split() == ["v1.2.3"]


@pytest.mark.parametrize(
    "version, test_cmd, message",
    [
        ("1.2.3", "true", "usage"),
        ("v9.9.9", "true", "no '## v9.9.9' section"),
        ("v1.2.3", "false", "tests failed"),
    ],
)
def test_release_refuses_and_creates_no_tag(release_repo, release, version, test_cmd, message):
    result = release(release_repo, version, test_cmd)

    assert result.returncode != 0
    assert message in result.stderr
    assert run(release_repo, "git", "tag").stdout == ""


def test_release_refuses_source_present_only_in_working_tree(release_repo, release):
    run(release_repo, "git", "rm", "--cached", "frontend/page.js")
    (release_repo / ".gitignore").write_text("frontend/page.js\n")
    run(release_repo, "git", "add", ".gitignore")
    commit(release_repo, "omit required source")
    run(release_repo, "git", "push", "-q", "origin", "master")
    assert (release_repo / "frontend" / "page.js").exists()

    result = release(release_repo)

    assert result.returncode != 0
    assert "committed frontend build failed" in result.stderr
    assert run(release_repo, "git", "tag").stdout == ""


def test_release_refuses_dirty_tree(release_repo, release):
    (release_repo / "CHANGELOG.md").write_text("changed")

    result = release(release_repo)

    assert result.returncode != 0
    assert "local changes" in result.stderr
    assert run(release_repo, "git", "tag").stdout == ""


def test_release_refuses_when_master_differs_from_the_remote(release_repo, release):
    commit(release_repo, "unpushed")

    result = release(release_repo)

    assert result.returncode != 0
    assert "differs from" in result.stderr
    assert run(release_repo, "git", "tag").stdout == ""


def test_skip_tags_are_not_auto_selected_but_can_be_requested(repo, select, signed_tag, state_dir):
    signed_tag(repo, "v1.0.0", 100, "good")
    signed_tag(repo, "v1.1.0", 100, "good")
    (state_dir / "skip-tags").write_text("v1.1.0\n")

    auto = select(repo)
    explicit = select(repo, "v1.1.0")

    assert auto.stdout.strip() == "v1.0.0"
    assert explicit.stdout.strip() == "v1.1.0"


# A young installed tag is still up to date: the first install takes the
# newest release without waiting out the delay.
@pytest.mark.parametrize("installed_age", [100, 1])
def test_automatic_selection_reports_up_to_date_instead_of_downgrading(
    repo, select, signed_tag, installed_age
):
    signed_tag(repo, "v1.0.0", 1, "good")
    commit(repo, "installed later")
    signed_tag(repo, "v1.1.0", installed_age, "good")
    commit(repo, "after the newest release")

    result = select(repo, installed_here=True)

    assert result.returncode == 3
    assert result.stdout == ""
    assert "up to date" in result.stderr


# The checkout's own tag passes: re-running update-to resumes an unfinished update.
@pytest.mark.parametrize(
    "ref, extra, ok",
    [
        ("v1.0.0", [], False),
        ("v1.0.0", ["--allow-downgrade"], True),
        ("v1.1.0", [], True),
    ],
)
def test_explicit_older_tag_needs_allow_downgrade(repo, select, signed_tag, ref, extra, ok):
    signed_tag(repo, "v1.0.0", 100, "good")
    commit(repo, "installed later")
    signed_tag(repo, "v1.1.0", 100, "good")

    result = select(repo, *extra, ref, installed_here=True)

    assert (result.returncode == 0) is ok, result.stderr
    assert result.stdout.strip() == ref * ok
    assert ("downgrade" in result.stderr) is (not ok)
