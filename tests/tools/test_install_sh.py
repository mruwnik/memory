import contextlib
import os
import pathlib
import pty
import re
import shutil
import stat
import subprocess
import threading

import pytest

from memory.common.modules import MODULES

REPO_ROOT = pathlib.Path(__file__).parents[2]

REQUIRED = {
    "DOMAIN": "memory.example.com",
    "S3_BACKUP": "off",
}
ADMIN = {
    "ADMIN_EMAIL": "owner@example.com",
    "ADMIN_PASSWORD": "admin-pw-Zk3v9QeXr7",
}
VOYAGE = {"VOYAGE_API_KEY": "voyage-key-Hq82LmPz41"}
S3 = {
    "S3_BACKUP_BUCKET": "my-backups",
    "S3_BACKUP_PREFIX": "kb",
    "S3_BACKUP_REGION": "us-east-1",
}
SUPPLIED = {
    "POSTGRES_PASSWORD": "pg-supplied-Aa11Bb22",
    "REDIS_PASSWORD": "redis-supplied-Ee55Ff66",
}
SECRET_FILES = ["postgres_password.txt"]


@pytest.fixture
def project(tmp_path):
    # tmp_path itself is shared with autouse fixtures that create directories in it
    tmp_path = tmp_path / "project"
    (tmp_path / "tools").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "tools" / "install.sh", tmp_path / "tools" / "install.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    # Answers network inspect from docker.networks, /setup/status from
    # setup_status and the api log from api.log.
    fake.write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp_path}/docker.log"\n'
        f'[ "$1" != compose ] || echo "$PWD" >> "{tmp_path}/docker.cwd"\n'
        f'[ "$1" != compose ] || echo "$REDIS_PASSWORD$POSTGRES_PASSWORD" >> "{tmp_path}/docker.env"\n'
        'case "$*" in\n'
        f'  "network inspect "*) grep -qx "$3" "{tmp_path}/docker.networks" 2>/dev/null ;;\n'
        f'  *setup/status*) cat "{tmp_path}/setup_status" 2>/dev/null ;;\n'
        f'  "compose logs"*) cat "{tmp_path}/api.log" 2>/dev/null ;;\n'
        'esac\n'
    )
    fake.chmod(0o755)
    getent = bin_dir / "getent"
    getent.write_text('#!/bin/sh\n[ "$*" != "group docker" ] || echo docker:x:4242:\n')
    getent.chmod(0o755)
    return tmp_path


def install(project, env, *args, cwd=None, interactive_input=None):
    flags = [] if interactive_input is not None else ["--non-interactive"]
    return subprocess.run(
        ["bash", str(project / "tools" / "install.sh"), *flags, *args],
        cwd=cwd or project,
        env={
            "PATH": f"{project / 'bin'}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(project),
            **env,
        },
        input=interactive_input,
        stdin=None if interactive_input is not None else subprocess.DEVNULL,
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
    env = {k: v for k, v in {**REQUIRED, **ADMIN}.items() if k != missing}

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


# Docker hands host-loopback connections to the api from kbnet's gateway, which
# the proxy override pins; only that address is trusted, not the subnet.
@pytest.mark.parametrize(
    "supplied, subnet, gateway",
    [
        ({}, "10.213.47.0/24", "10.213.47.1"),
        ({"KBNET_SUBNET": "10.99.0.0/24"}, "10.99.0.0/24", "10.99.0.1"),
        ({"KBNET_SUBNET": "10.99.0.0/24", "KBNET_GATEWAY": "10.99.0.254"}, "10.99.0.0/24", "10.99.0.254"),
    ],
)
def test_writes_reverse_proxy_settings(project, supplied, subnet, gateway):
    result = install(project, {**REQUIRED, **supplied})

    assert result.returncode == 0, result.stderr

    env = read_env(project)
    assert env["SERVER_URL"] == "https://memory.example.com"
    assert env["API_PORT"] == "127.0.0.1:8000"
    assert env["COMPOSE_FILE"] == "docker-compose.yaml:deploy/aws/compose.proxy.yaml"
    assert (env["KBNET_SUBNET"], env["KBNET_GATEWAY"]) == (subnet, gateway)
    assert env["FORWARDED_ALLOW_IPS"] == f"127.0.0.1,::1,{gateway}"
    assert env["RATE_LIMIT_TRUSTED_PROXIES"] == f"127.0.0.1,::1,{gateway}"


# The claude overlay is appended, never replacing what is there (the proxy overlay).
@pytest.mark.parametrize(
    "existing, modules, compose_file",
    [
        ("", "", "docker-compose.yaml:deploy/aws/compose.proxy.yaml"),
        ("", "claude_containers,teams", "docker-compose.yaml:deploy/aws/compose.proxy.yaml:docker-compose.claude.yaml"),
        ("COMPOSE_FILE=custom.yaml\n", "", "custom.yaml"),
        ("COMPOSE_FILE=custom.yaml\n", "claude_containers", "custom.yaml:docker-compose.claude.yaml"),
    ],
)
def test_non_interactive_compose_file_and_modules(project, existing, modules, compose_file):
    (project / ".env").write_text(existing)

    result = install(project, {**REQUIRED, "ENABLE_MODULES": modules})

    assert result.returncode == 0, result.stderr
    env = read_env(project)
    assert (env["COMPOSE_FILE"], env["ENABLE_MODULES"]) == (compose_file, modules)
    assert ("DOCKER_GID" in env) == ("claude_containers" in modules)


def test_warns_when_compose_file_lacks_the_proxy_overlay(project):
    (project / ".env").write_text("COMPOSE_FILE=custom.yaml\n")

    result = install(project, REQUIRED)

    assert result.returncode == 0, result.stderr
    assert "compose.proxy.yaml" in result.stdout


def test_supplied_secrets_are_used(project):
    result = install(project, {**REQUIRED, **SUPPLIED})

    assert result.returncode == 0, result.stderr

    env = read_env(project)
    assert (project / "secrets" / "postgres_password.txt").read_text() == SUPPLIED["POSTGRES_PASSWORD"]
    assert env["REDIS_PASSWORD"] == SUPPLIED["REDIS_PASSWORD"]


# Compose needs the file even without a key, which an admin then sets in the web UI.
@pytest.mark.parametrize(
    "env, stdin, key",
    [
        (REQUIRED, None, ""),
        ({**REQUIRED, **VOYAGE}, None, VOYAGE["VOYAGE_API_KEY"]),
        # Every prompt left blank, then no backups, no SSH keys and the default modules.
        ({}, "\n" * 5 + "N\n" + "n\n" + "\n\n", ""),
    ],
    ids=["non-interactive-without", "non-interactive-with", "interactive-skipped"],
)
def test_the_voyage_key_is_optional_and_only_in_its_secret_file(project, env, stdin, key):
    result = install(project, env, interactive_input=stdin)

    assert result.returncode == 0, result.stderr
    voyage = project / "secrets" / "voyage_key.txt"
    assert (voyage.read_text(), mode(voyage)) == (key, 0o600)
    assert "VOYAGE_API_KEY" not in read_env(project)


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
    result = install(project, {**REQUIRED, **ADMIN, **SUPPLIED, **VOYAGE})

    generated = [
        p.read_text() for p in (project / "secrets").iterdir() if p.read_text()
    ]
    haystack = result.stdout + result.stderr + (project / "docker.log").read_text()
    leaked = [
        s
        for s in [*generated, *SUPPLIED.values(), ADMIN["ADMIN_PASSWORD"], VOYAGE["VOYAGE_API_KEY"]]
        if s in haystack
    ]
    assert leaked == []


def test_migrates_then_starts_the_api_then_creates_first_user_with_password_via_environment(project):
    install(project, {**REQUIRED, **ADMIN})

    calls = (project / "docker.log").read_text().splitlines()
    add_user = [c for c in calls if "add_user.py" in c]
    assert len(add_user) == 1
    # The api's healthcheck passes on an empty schema, so only this order gives add_user.py its tables.
    order = [calls.index(c) for c in ["compose run --rm migrate", "compose up -d --wait api", add_user[0]]]
    assert order == sorted(order)
    assert "--email owner@example.com" in add_user[0]
    assert "-e ADD_USER_PASSWORD" in add_user[0]
    assert "--admin" in add_user[0]
    # No key to print, so nothing secret can reach the caller's log.
    assert "--no-api-key" in add_user[0]


@pytest.mark.parametrize("existing, created", [("", True), ("memory-api-prod", False)])
@pytest.mark.parametrize(
    "env, stdin",
    [
        ({**REQUIRED, "ENABLE_MODULES": "claude_containers"}, None),
        # Blank answers, two rejected backup answers, no backups, no SSH keys, containers.
        ({}, "\n" * 7 + "n\n" + "n\n" + "y\n\n"),
    ],
    ids=["non-interactive", "interactive"],
)
def test_creates_the_external_api_network_once_with_containers(project, existing, created, env, stdin):
    # The fake inspects successfully only the networks named in docker.networks.
    (project / "docker.networks").write_text(existing)

    result = install(project, env, interactive_input=stdin)

    assert result.returncode == 0, result.stderr
    calls = (project / "docker.log").read_text().splitlines()
    assert ("network create memory-api-prod" in calls) == created
    assert calls[:1] == ["network inspect memory-api-prod"]


@pytest.mark.parametrize(
    "env, stdin",
    [(REQUIRED, None), ({}, "\n" * 5 + "n\n" + "n\n" + "\n\n")],
    ids=["non-interactive", "interactive"],
)
def test_no_api_network_without_containers(project, env, stdin):
    (project / "docker.log").touch()

    result = install(project, env, interactive_input=stdin)

    assert result.returncode == 0, result.stderr
    assert "network" not in (project / "docker.log").read_text()


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


@pytest.mark.parametrize(
    "var", ["DOMAIN", "ADMIN_EMAIL", "VOYAGE_API_KEY", "POSTGRES_PASSWORD", "ENABLE_MODULES"]
)
@pytest.mark.parametrize("bad", ["a b", "a$b", "a|b"])
def test_rejects_unsafe_values_naming_variable_but_not_value(project, var, bad):
    result = install(project, {**REQUIRED, **ADMIN, **VOYAGE, var: bad})

    assert result.returncode != 0
    assert var in result.stderr
    assert bad not in result.stdout + result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


def test_rerun_tightens_permissions_and_keeps_existing_optional_keys(project):
    (project / "secrets").mkdir()
    (project / "secrets" / "secrets_encryption_key.txt").write_text("hand-written")
    (project / "secrets" / "postgres_password.txt").write_text("old-pg")
    (project / ".env").write_text("REDIS_PASSWORD=old\n")
    for f in [project / ".env", *(project / "secrets").iterdir()]:
        f.chmod(0o644)
    # rotate-keys.sh keeps superseded keys here.
    (project / "secrets" / "previous").mkdir(mode=0o700)

    result = install(project, REQUIRED)

    assert result.returncode == 0, result.stderr
    assert (project / "secrets" / "secrets_encryption_key.txt").read_text() == "hand-written"
    files = [project / ".env", *(project / "secrets").glob("*.txt")]
    assert {mode(f) for f in files} == {0o600}
    assert mode(project / "secrets" / "previous") == 0o700


BACKUP_VARS = ["S3_BACKUP_BUCKET", "S3_BACKUP_PREFIX", "S3_BACKUP_REGION", "BACKUP_ENCRYPTION_KEY"]
# App name, Voyage, API port, deploy host, deploy dir
PROMPTS_BEFORE_BACKUP = "\n" * 5
PROMPTS_AFTER_BACKUP = "n\n" + "\n\n"  # no SSH keys, no containers, one user


def test_non_interactive_without_backup_choice_fails_naming_variables_and_writes_nothing(project):
    env = {k: v for k, v in REQUIRED.items() if k != "S3_BACKUP"}

    result = install(project, env)

    assert result.returncode != 0
    assert "S3_BACKUP_BUCKET" in result.stderr
    assert "S3_BACKUP=off" in result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


@pytest.mark.parametrize("bad", ["on", "yes", "OFF "])
def test_non_interactive_rejects_unknown_backup_choice(project, bad):
    result = install(project, {**REQUIRED, "S3_BACKUP": bad})

    assert result.returncode != 0
    assert "S3_BACKUP" in result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


def test_backup_off_configures_no_backup_destination(project):
    result = install(project, REQUIRED)

    assert result.returncode == 0, result.stderr
    env = read_env(project)
    assert [v for v in BACKUP_VARS if v in env] == []


def test_non_interactive_bucket_enables_backups_with_generated_key(project):
    env = {k: v for k, v in REQUIRED.items() if k != "S3_BACKUP"}

    result = install(project, {**env, **S3})

    assert result.returncode == 0, result.stderr
    written = read_env(project)
    assert {k: written[k] for k in S3} == S3
    assert len(written["BACKUP_ENCRYPTION_KEY"]) == 32
    assert written["BACKUP_ENCRYPTION_KEY"] not in result.stdout + result.stderr


def test_non_interactive_bucket_uses_supplied_key_and_defaults_prefix(project):
    env = {k: v for k, v in REQUIRED.items() if k != "S3_BACKUP"}

    result = install(
        project, {**env, "S3_BACKUP_BUCKET": "b", "BACKUP_ENCRYPTION_KEY": "supplied-Kk99"}
    )

    assert result.returncode == 0, result.stderr
    written = read_env(project)
    assert written["BACKUP_ENCRYPTION_KEY"] == "supplied-Kk99"
    assert written["S3_BACKUP_PREFIX"] == "memory"


@pytest.mark.parametrize("var", ["S3_BACKUP_BUCKET", "S3_BACKUP_PREFIX", "BACKUP_ENCRYPTION_KEY"])
def test_rejects_unsafe_backup_values_naming_variable_but_not_value(project, var):
    env = {k: v for k, v in REQUIRED.items() if k != "S3_BACKUP"}

    result = install(project, {**env, "S3_BACKUP_BUCKET": "b", var: "a b"})

    assert result.returncode != 0
    assert var in result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param("S3_BACKUP=off\n", id="off"),
        pytest.param("S3_BACKUP_BUCKET=old\nBACKUP_ENCRYPTION_KEY=oldkey\n", id="bucket"),
    ],
)
def test_non_interactive_rerun_keeps_existing_choice_without_it_being_supplied(project, existing):
    (project / ".env").write_text(existing)
    env = {k: v for k, v in REQUIRED.items() if k != "S3_BACKUP"}

    result = install(project, env)

    assert result.returncode == 0, result.stderr
    kept = read_env(project)
    assert {k: kept[k] for k in read_env_text(existing)} == read_env_text(existing)


def read_env_text(text):
    return dict(line.split("=", 1) for line in text.splitlines())


@pytest.mark.parametrize(
    "answers, expected",
    [
        pytest.param("n\n", {"S3_BACKUP": "off"}, id="no"),
        pytest.param("\nmaybe\nN\n", {"S3_BACKUP": "off"}, id="reprompts-until-valid"),
        pytest.param(
            "y\nmy-bucket\n\nus-west-2\n",
            {
                "S3_BACKUP_BUCKET": "my-bucket",
                "S3_BACKUP_PREFIX": "memory",
                "S3_BACKUP_REGION": "us-west-2",
            },
            id="yes",
        ),
    ],
)
def test_interactive_requires_an_explicit_backup_choice(project, answers, expected):
    stdin = PROMPTS_BEFORE_BACKUP + answers + PROMPTS_AFTER_BACKUP

    result = install(project, {}, interactive_input=stdin)

    assert result.returncode == 0, result.stderr
    written = read_env(project)
    assert {k: written[k] for k in expected} == expected
    assert ("BACKUP_ENCRYPTION_KEY" in written) == ("S3_BACKUP_BUCKET" in expected)


@pytest.mark.parametrize(
    "existing", ["S3_BACKUP=off\n", "S3_BACKUP_BUCKET=old\nBACKUP_ENCRYPTION_KEY=oldkey\n"]
)
def test_interactive_rerun_keeps_choice_without_asking(project, existing):
    (project / ".env").write_text(existing)
    # Fewer blank answers than prompts would be consumed if it asked again
    stdin = PROMPTS_BEFORE_BACKUP + PROMPTS_AFTER_BACKUP

    result = install(project, {}, interactive_input=stdin)

    assert result.returncode == 0, result.stderr
    assert "back up" not in result.stdout.lower()
    kept = read_env(project)
    assert {k: kept[k] for k in read_env_text(existing)} == read_env_text(existing)


@pytest.mark.parametrize(
    "existing, supplied",
    [
        ("S3_BACKUP_BUCKET=old\nBACKUP_ENCRYPTION_KEY=k\n", {"S3_BACKUP_BUCKET": "new"}),
        ("S3_BACKUP_BUCKET=old\nBACKUP_ENCRYPTION_KEY=k\n", {"S3_BACKUP": "off"}),
    ],
)
def test_rerun_warns_when_supplied_choice_differs_from_env(project, existing, supplied):
    (project / ".env").write_text(existing)
    env = {k: v for k, v in REQUIRED.items() if k != "S3_BACKUP"}

    result = install(project, {**env, **supplied})

    assert result.returncode == 0, result.stderr
    assert "Ignoring supplied" in result.stdout
    assert read_env(project)["S3_BACKUP_BUCKET"] == "old"


def test_interactive_validates_env_supplied_backup_key(project):
    stdin = PROMPTS_BEFORE_BACKUP + "y\nb\n\n\n"

    result = install(project, {"BACKUP_ENCRYPTION_KEY": "a b"}, interactive_input=stdin)

    assert result.returncode != 0
    assert "BACKUP_ENCRYPTION_KEY" in result.stderr
    assert "a b" not in result.stdout + result.stderr


CLAUDE_COMPOSE = "docker-compose.yaml:docker-compose.claude.yaml"


# Teams are only asked about with several users.
@pytest.mark.parametrize(
    "answers, modules, compose_file, docker_gid",
    [
        pytest.param("\n\n", "", None, None, id="defaults"),
        pytest.param("y\nN\n", "claude_containers", CLAUDE_COMPOSE, "4242", id="containers"),
        pytest.param("n\ny\nN\n", "multi_user", None, None, id="multi-user"),
        pytest.param("y\ny\ny\n", "claude_containers,multi_user,teams", CLAUDE_COMPOSE, "4242", id="all"),
    ],
)
def test_interactive_module_questions(project, answers, modules, compose_file, docker_gid):
    stdin = PROMPTS_BEFORE_BACKUP + "n\n" + "n\n" + answers

    result = install(project, {}, interactive_input=stdin)

    assert result.returncode == 0, result.stderr
    env = read_env(project)
    assert (env["ENABLE_MODULES"], env.get("COMPOSE_FILE"), env.get("DOCKER_GID")) == (
        modules,
        compose_file,
        docker_gid,
    )


def test_interactive_rerun_keeps_modules_without_asking(project):
    (project / ".env").write_text("ENABLE_MODULES=teams\n")

    result = install(project, {}, interactive_input=PROMPTS_BEFORE_BACKUP + "n\n" + "n\n")

    assert result.returncode == 0, result.stderr
    assert read_env(project)["ENABLE_MODULES"] == "teams"


@pytest.mark.parametrize("admin", [{"ADMIN_EMAIL": "owner@example.com"}, {"ADMIN_PASSWORD": "pw-Zk3v9QeXr7"}])
def test_admin_email_and_password_go_together(project, admin):
    result = install(project, {**REQUIRED, **admin})

    assert result.returncode != 0
    assert "ADMIN_EMAIL" in result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]


TOKEN_LINE = (
    "api-1  | WARNING memory.api.setup: No account exists yet. "
    "Create it at https://memory.example.com/ui/setup with setup token: Tok_en-9xY\n"
)


def install_on_terminal(project, env):
    """Runs a non-interactive install with stdout on a pseudo-terminal."""
    main, child = pty.openpty()
    chunks = []

    def drain():
        # The read fails (EIO) or returns b"" once the child side is closed
        with contextlib.suppress(OSError):
            while data := os.read(main, 4096):
                chunks.append(data)

    reader = threading.Thread(target=drain)
    reader.start()
    result = subprocess.run(
        ["bash", str(project / "tools" / "install.sh"), "--non-interactive"],
        cwd=project,
        env={"PATH": f"{project / 'bin'}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(project), **env},
        stdin=subprocess.DEVNULL,
        stdout=child,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    os.close(child)
    reader.join(timeout=10)
    os.close(main)
    return result.returncode, b"".join(chunks).decode()


NEEDS_ACCOUNT = {"setup_status": '{"needs_account":true}'}


# The token shows only on a terminal, and only while the api says the instance is
# unclaimed; otherwise the command that shows it, unless there is nothing to claim.
@pytest.mark.parametrize(
    "files, on_terminal, token_shown, command_shown",
    [
        pytest.param({**NEEDS_ACCOUNT, "api.log": TOKEN_LINE}, True, True, False, id="terminal"),
        pytest.param({**NEEDS_ACCOUNT, "api.log": ""}, True, False, True, id="token-not-in-log"),
        pytest.param({**NEEDS_ACCOUNT, "api.log": TOKEN_LINE}, False, False, True, id="not-a-terminal"),
        pytest.param({"api.log": TOKEN_LINE}, True, False, True, id="status-probe-fails"),
        pytest.param(
            {"setup_status": '{"needs_account":false}', "api.log": TOKEN_LINE}, True, False, False, id="claimed"
        ),
    ],
)
def test_without_admin_points_at_the_setup_token(project, files, on_terminal, token_shown, command_shown):
    for name, content in files.items():
        (project / name).write_text(content)

    run = install_on_terminal if on_terminal else lambda p, e: astuple(install(p, e))
    returncode, stdout = run(project, REQUIRED)

    assert returncode == 0, stdout
    calls = (project / "docker.log").read_text()
    assert "add_user.py" not in calls
    assert "compose run --rm migrate" in calls
    assert ("Tok_en-9xY" in stdout) == token_shown
    assert ("docker compose logs api" in stdout) == command_shown


def astuple(result):
    return result.returncode, result.stdout


def test_known_modules_match_the_app():
    script = (REPO_ROOT / "tools" / "install.sh").read_text()

    known = re.search(r'^KNOWN_MODULES="(.*)"$', script, re.M)

    assert known and set(known.group(1).split()) == set(MODULES)


@pytest.mark.parametrize("modules", ["claude_container", "teams,projects"])
def test_unknown_module_fails_before_writing_anything(project, modules):
    result = install(project, {**REQUIRED, "ENABLE_MODULES": modules})

    assert result.returncode != 0
    assert "ENABLE_MODULES" in result.stderr
    assert sorted(p.name for p in project.iterdir()) == ["bin", "tools"]
