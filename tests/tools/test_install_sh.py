import os
import pathlib
import shutil
import stat
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]

REQUIRED = {
    "DOMAIN": "memory.example.com",
    "ADMIN_EMAIL": "owner@example.com",
    "ADMIN_PASSWORD": "admin-pw-Zk3v9QeXr7",
    "VOYAGE_API_KEY": "voyage-key-Hq82LmPz41",
}
SUPPLIED = {
    "POSTGRES_PASSWORD": "pg-supplied-Aa11Bb22",
    "JWT_SECRET": "jwt-supplied-Cc33Dd44",
    "REDIS_PASSWORD": "redis-supplied-Ee55Ff66",
}
SECRET_FILES = ["postgres_password.txt", "jwt_secret.txt", "voyage_key.txt"]


@pytest.fixture
def project(tmp_path):
    # tmp_path itself is shared with autouse fixtures that create directories in it
    tmp_path = tmp_path / "project"
    (tmp_path / "tools").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "tools" / "install.sh", tmp_path / "tools" / "install.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp_path}/docker.log"\n'
        f'echo "$PWD" >> "{tmp_path}/docker.cwd"\n'
        f'echo "$REDIS_PASSWORD$POSTGRES_PASSWORD$JWT_SECRET" >> "{tmp_path}/docker.env"\n'
    )
    fake.chmod(0o755)
    return tmp_path


def install(project, env, *args, cwd=None):
    return subprocess.run(
        ["bash", str(project / "tools" / "install.sh"), "--non-interactive", *args],
        cwd=cwd or project,
        env={
            "PATH": f"{project / 'bin'}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(project),
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def read_env(project):
    lines = (project / ".env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_value_fails_naming_it_and_writes_nothing(project, missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}

    result = install(project, env)

    assert result.returncode != 0
    assert missing in result.stdout + result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


def test_generates_secrets_with_owner_only_permissions(project):
    result = install(project, REQUIRED)

    assert result.returncode == 0, result.stderr
    env = read_env(project)
    files = [project / "secrets" / name for name in SECRET_FILES]
    assert all(f.read_text() for f in files)
    assert {mode(f) for f in files} == {0o600}
    assert mode(project / ".env") == 0o600
    assert len(env["REDIS_PASSWORD"]) == 32
    assert mode(project / "secrets") == 0o700
    assert "BACKUP_ENCRYPTION_KEY" not in env


def test_writes_reverse_proxy_settings(project):
    result = install(project, REQUIRED)

    assert result.returncode == 0, result.stderr

    env = read_env(project)
    assert env["SERVER_URL"] == "https://memory.example.com"
    assert env["API_PORT"] == "127.0.0.1:8000"
    assert env["FORWARDED_ALLOW_IPS"] == "127.0.0.1,::1"
    assert env["RATE_LIMIT_TRUSTED_PROXIES"] == "127.0.0.1,::1"


def test_supplied_secrets_are_used(project):
    result = install(project, {**REQUIRED, **SUPPLIED})

    assert result.returncode == 0, result.stderr

    env = read_env(project)
    assert (project / "secrets" / "postgres_password.txt").read_text() == SUPPLIED["POSTGRES_PASSWORD"]
    assert (project / "secrets" / "jwt_secret.txt").read_text() == SUPPLIED["JWT_SECRET"]
    assert env["REDIS_PASSWORD"] == SUPPLIED["REDIS_PASSWORD"]
    assert (project / "secrets" / "voyage_key.txt").read_text() == REQUIRED["VOYAGE_API_KEY"]


def test_rerun_keeps_existing_secrets_even_if_different_ones_are_supplied(project):
    install(project, REQUIRED)
    before = {
        p.name: p.read_text() for p in (project / "secrets").iterdir()
    } | read_env(project)

    result = install(project, {**REQUIRED, **SUPPLIED})

    assert result.returncode == 0, result.stderr
    after = {
        p.name: p.read_text() for p in (project / "secrets").iterdir()
    } | read_env(project)
    assert after == before


def test_output_and_docker_args_contain_no_secrets(project):
    result = install(project, {**REQUIRED, **SUPPLIED})

    generated = [
        p.read_text() for p in (project / "secrets").iterdir() if p.read_text()
    ]
    haystack = result.stdout + result.stderr + (project / "docker.log").read_text()
    leaked = [
        s
        for s in [*generated, *SUPPLIED.values(), REQUIRED["ADMIN_PASSWORD"], REQUIRED["VOYAGE_API_KEY"]]
        if s in haystack
    ]
    assert leaked == []


def test_creates_first_user_with_password_passed_via_environment(project):
    install(project, REQUIRED)

    calls = (project / "docker.log").read_text().splitlines()
    add_user = [c for c in calls if "add_user.py" in c]
    assert len(add_user) == 1
    assert "--email owner@example.com" in add_user[0]
    assert "-e ADD_USER_PASSWORD" in add_user[0]


def test_runs_compose_in_the_project_directory_whatever_the_caller_cwd(project, tmp_path):
    result = install(project, REQUIRED, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    cwds = set((project / "docker.cwd").read_text().split())
    assert {pathlib.Path(c).resolve() for c in cwds} == {project.resolve()}


def test_forces_loopback_api_port_over_existing_value(project):
    (project / ".env").write_text("API_PORT=8000\n")

    install(project, REQUIRED)

    assert read_env(project)["API_PORT"] == "127.0.0.1:8000"


@pytest.mark.parametrize("earlier_runs", [[], [REQUIRED]])
def test_supplied_secrets_do_not_reach_compose_environment(project, earlier_runs):
    for env in earlier_runs:
        install(project, env)
    (project / "docker.env").unlink(missing_ok=True)

    install(project, {**REQUIRED, **SUPPLIED})

    assert (project / "docker.env").read_text().strip() == ""


@pytest.mark.parametrize("var", ["DOMAIN", "ADMIN_EMAIL", "VOYAGE_API_KEY", "JWT_SECRET"])
@pytest.mark.parametrize("bad", ["a b", "a$b", "a|b"])
def test_rejects_unsafe_values_naming_variable_but_not_value(project, var, bad):
    result = install(project, {**REQUIRED, var: bad})

    assert result.returncode != 0
    assert var in result.stderr
    assert bad not in result.stdout + result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


def test_rerun_tightens_permissions_and_keeps_existing_optional_keys(project):
    (project / "secrets").mkdir()
    (project / "secrets" / "openai_key.txt").write_text("hand-written")
    (project / "secrets" / "postgres_password.txt").write_text("old-pg")
    (project / ".env").write_text("REDIS_PASSWORD=old\n")
    for f in [project / ".env", *(project / "secrets").iterdir()]:
        f.chmod(0o644)

    result = install(project, REQUIRED)

    assert result.returncode == 0, result.stderr
    assert (project / "secrets" / "openai_key.txt").read_text() == "hand-written"
    modes = {mode(f) for f in [project / ".env", *(project / "secrets").iterdir()]}
    assert modes == {0o600}
