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
def clone(tmp_path):
    """A clone at v0.0.9 with a newer v0.0.10, whose origin is an empty bare repo."""
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", str(REPO_ROOT), str(clone))
    git(tmp_path, "init", "--quiet", "--bare", str(tmp_path / "origin.git"))
    git(clone, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    shutil.copy(REPO_ROOT / "tools" / "update.sh", clone / "tools" / "update.sh")
    git(clone, "add", "tools/update.sh")
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "update.sh under test")
    git(clone, "tag", "v0.0.9")
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "next release")
    git(clone, "tag", "v0.0.10")
    git(clone, "checkout", "--quiet", "--detach", "v0.0.9")
    return clone


def dry_run(clone):
    result = subprocess.run(
        ["bash", "tools/update.sh", "--dry-run"],
        cwd=clone,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "COMPOSE": "echo compose"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout + result.stderr


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
