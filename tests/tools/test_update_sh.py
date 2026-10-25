import pathlib
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parents[2]


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path, docker_bin):
    """A clone at v0.0.9 with a newer v0.0.10, whose origin is an empty bare repo."""
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", "--no-tags", str(REPO_ROOT), str(clone))
    git(tmp_path, "init", "--quiet", "--bare", str(tmp_path / "origin.git"))
    git(clone, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    # The repo's own release tags would outrank the test's.
    real_tags = git(clone, "tag", "--list").split()
    if real_tags:
        git(clone, "tag", "-d", *real_tags)
    for script in ["update.sh", "select_release.sh", "compose_project.sh"]:
        shutil.copy(REPO_ROOT / "tools" / script, clone / "tools" / script)
    (clone / "tools" / "ops").mkdir(exist_ok=True)
    shutil.copy(REPO_ROOT / "tools" / "ops" / "lib.sh", clone / "tools" / "ops" / "lib.sh")
    git(clone, "add", "tools")
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "update.sh under test")
    git(clone, "tag", "v0.0.9")
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "next release")
    git(clone, "tag", "v0.0.10")
    git(clone, "checkout", "--quiet", "--detach", "v0.0.9")
    return clone


def update(clone, *args, cwd=None, path="", lock_file=None, root=None):
    root = root or clone
    result = subprocess.run(
        ["bash", str(root / "tools" / "update.sh"), *args],
        cwd=cwd or root,
        env={
            "PATH": f"{path}{clone.parent / 'docker-bin'}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "COMPOSE": "echo compose",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "STATE_DIR": str(clone.parent / "state"),
            "LOCK_FILE": lock_file or str(clone.parent / "nightly.lock"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout + result.stderr


def dry_run(clone, *args, cwd=None):
    return update(clone, "--dry-run", *args, cwd=cwd)


def write_state(clone, target_sha):
    (clone / "backups").mkdir()
    (clone / "backups" / "update-in-progress").write_text(
        f"PREVIOUS=v0.0.1\nPREVIOUS_SHA=abc\nTARGET_SHA={target_sha}\nBACKUP=\nMIGRATED=0\n"
    )


def test_dry_run_plans_update_to_newest_tag_without_changing_anything(clone):
    head_before = git(clone, "rev-parse", "HEAD")

    code, output = dry_run(clone)

    assert code == 0, output
    assert "-> v0.0.10" in output
    assert "+ echo compose run --rm --no-deps migrate" in output
    assert "Dry run complete" in output
    assert git(clone, "rev-parse", "HEAD") == head_before
    assert not (clone / "backups").exists()


def test_dry_run_stops_every_app_service_built_from_the_repo(clone):
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    # migrate is a one-shot `compose run`, not a running service.
    app_services = {
        name
        for name, service in compose["services"].items()
        if "build" in service and name != "migrate"
    }

    code, output = dry_run(clone)

    assert code == 0, output
    stop = next(
        line for line in output.splitlines() if line.startswith("+ echo compose stop ")
    )
    assert set(stop.split()[4:]) == app_services


def test_dry_run_checks_settings_on_the_new_images_before_stopping_anything(clone):
    code, output = dry_run(clone)

    assert code == 0, output
    lines = output.splitlines()
    check = lines.index(
        "+ echo compose run --rm --no-deps --entrypoint python worker"
        " -m memory.common.settings worker"
    )
    stop = next(i for i, line in enumerate(lines) if line.startswith("+ echo compose stop "))
    assert check < stop


def test_stale_update_state_is_refused(clone):
    write_state(clone, git(clone, "rev-parse", "v0.0.10"))

    code, output = dry_run(clone)

    assert code != 0
    assert "stale update state" in output


def test_update_state_for_current_checkout_is_resumed(clone):
    write_state(clone, git(clone, "rev-parse", "HEAD"))

    code, output = dry_run(clone)

    assert code == 0, output
    assert "rollback target stays v0.0.1" in output


def test_signed_old_tag_is_planned_when_signatures_are_required(clone, allowed, signed_tag):
    git(clone, "checkout", "--quiet", "--detach", "v0.0.10")
    signed_tag(clone, "v0.0.11", 100, "good")
    git(clone, "checkout", "--quiet", "--detach", "v0.0.9")

    code, output = dry_run(clone, "--require-signed", str(allowed), "--min-age-hours", "48")

    assert code == 0, output
    assert "-> v0.0.11" in output


@pytest.mark.parametrize("explicit", [[], ["v0.0.10"]], ids=["auto", "explicit"])
def test_unsigned_tags_are_refused_before_anything_changes(clone, allowed, explicit):
    head_before = git(clone, "rev-parse", "HEAD")

    code, output = dry_run(
        clone, "--require-signed", str(allowed), "--min-age-hours", "48", *explicit
    )

    assert code != 0
    assert "Dry run complete" not in output
    assert git(clone, "rev-parse", "HEAD") == head_before


def test_relative_allowed_signers_path_is_resolved_from_the_callers_directory(
    clone, allowed, signed_tag
):
    signed_tag(clone, "v0.0.11", 100, "good")

    code, output = dry_run(
        clone, "--require-signed", allowed.name, "--min-age-hours", "48", cwd=allowed.parent
    )

    assert code == 0, output


def test_dry_run_does_not_record_first_sightings(clone, allowed, signed_tag):
    signed_tag(clone, "v0.0.11", 100, "good", seen=None)

    code, output = dry_run(clone, "--require-signed", str(allowed), "--min-age-hours", "48")

    assert "Already up to date" in output, output
    assert not (clone.parent / "state" / "seen-tags").exists()


def test_missing_allowed_signers_file_is_named_in_the_error(clone):
    code, output = dry_run(clone, "--require-signed", "nope", "--min-age-hours", "48")

    assert code != 0
    assert "nope" in output


def test_update_to_the_checked_out_commit_does_nothing(clone):
    code, output = dry_run(clone, "v0.0.9")

    assert code == 0, output
    assert "Already at" in output
    assert "migrate" not in output


def test_up_to_date_with_an_unfinished_update_reports_failure(clone, allowed, signed_tag):
    signed_tag(clone, "v0.0.11", 100, "good")  # tag at HEAD: installed
    write_state(clone, git(clone, "rev-parse", "HEAD"))

    code, output = dry_run(clone, "--require-signed", str(allowed), "--min-age-hours", "48")

    assert code != 0
    assert "did not finish" in output


@pytest.mark.parametrize("extra, ok", [([], False), (["--allow-downgrade"], True)])
def test_explicit_older_release_needs_allow_downgrade(clone, allowed, signed_tag, extra, ok):
    signed_tag(clone, "v0.0.8", 100, "good")
    git(clone, "checkout", "--quiet", "--detach", "v0.0.10")

    code, output = dry_run(
        clone, "--require-signed", str(allowed), "--min-age-hours", "48", *extra, "v0.0.8"
    )

    assert (code == 0) is ok, output
    assert ("-> v0.0.8" in output) is ok


def test_update_refuses_while_the_nightly_lock_is_held(clone, lock_bin, held_lock):
    head_before = git(clone, "rev-parse", "HEAD")

    code, output = update(clone, path=f"{lock_bin}:")

    assert code != 0
    assert "busy" in output
    assert not (clone / "backups").exists()
    assert git(clone, "rev-parse", "HEAD") == head_before


def test_update_runs_without_flock_or_a_lock_directory(clone):
    code, output = update(clone, lock_file=str(clone.parent / "missing" / "nightly.lock"))

    assert "busy" not in output
    assert "No such file" not in output


def read_project_name(clone):
    lines = (clone / ".env").read_text().splitlines()
    return [line.split("=", 1)[1] for line in lines if line.startswith("COMPOSE_PROJECT_NAME=")]


# On EC2 /opt/memory is a symlink to the checkout: a run from the resolved
# path must keep the project the stack was started under via the link.
@pytest.mark.parametrize("env", ["DOMAIN=example.com", "COMPOSE_PROJECT_NAME=\nDOMAIN=example.com\n"])
def test_update_pins_the_running_project_name_in_env(clone, env):
    (clone.parent / "memory").symlink_to(clone)
    (clone.parent / "containers").write_text(f"memory {clone.parent / 'memory'}\n")
    (clone / ".env").write_text(env)

    update(clone)

    assert read_project_name(clone) == ["memory"]
    assert "DOMAIN=example.com" in (clone / ".env").read_text().splitlines()


def test_update_keeps_a_project_name_already_in_env(clone):
    (clone.parent / "containers").write_text(f"memory {clone}\n")
    (clone / ".env").write_text("COMPOSE_PROJECT_NAME=chris\n")

    update(clone)

    assert read_project_name(clone) == ["chris"]


@pytest.mark.parametrize(
    "state, message",
    [
        ({"containers": "memory {clone}\napp {clone}\n"}, "No single compose project"),
        ({"docker.fails": ""}, "Cannot connect to the Docker daemon"),
    ],
)
def test_update_refuses_when_the_project_is_unclear(clone, state, message):
    head_before = git(clone, "rev-parse", "HEAD")
    for name, text in state.items():
        (clone.parent / name).write_text(text.format(clone=clone))

    code, output = update(clone)

    assert code != 0
    assert message in output
    assert "Set COMPOSE_PROJECT_NAME in .env" in output
    assert not (clone / ".env").exists()
    assert git(clone, "rev-parse", "HEAD") == head_before


def test_dry_run_does_not_pin_the_project_name(clone):
    (clone.parent / "containers").write_text(f"memory {clone}\n")

    code, output = dry_run(clone)

    assert code == 0, output
    assert not (clone / ".env").exists()
