"""Secrets in docker-compose.yaml stay off process command lines, which every
local user can read with `ps`."""

import pathlib
import re
import subprocess

import pytest
import yaml

COMPOSE = yaml.safe_load((pathlib.Path(__file__).parents[2] / "docker-compose.yaml").read_text())
SECRET_VAR = re.compile(r"\$\{?\w*(PASSWORD|SECRET|KEY|TOKEN)\w*")
STUB_REDIS = '#!/bin/sh\necho "args: $*"\ncat "$1"\n'


def argv_fields(service: dict) -> list:
    return [service.get("command"), service.get("entrypoint"), (service.get("healthcheck") or {}).get("test")]


@pytest.mark.parametrize("name", sorted(COMPOSE["services"]))
def test_no_secret_is_interpolated_into_a_command_line(name):
    fields = argv_fields(COMPOSE["services"][name])

    assert not SECRET_VAR.search(str(fields))


@pytest.mark.parametrize("password", ["plainAlnum123", 'a b"c\\d$e'])
def test_redis_gets_its_password_from_a_private_config_file(tmp_path, password):
    (tmp_path / "redis-server").write_text(STUB_REDIS)
    (tmp_path / "redis-server").chmod(0o755)
    conf = tmp_path / "redis.conf"
    service = COMPOSE["services"]["redis"]
    script = service["command"][0].replace("$$", "$").replace("/tmp/redis.conf", str(conf))

    result = subprocess.run(
        [*service["entrypoint"], script],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "REDISCLI_AUTH": password},
        capture_output=True,
        text=True,
        check=True,
    )

    escaped = password.replace("\\", "\\\\").replace('"', '\\"')
    assert result.stdout.splitlines() == [
        f"args: {conf}",
        f'requirepass "{escaped}"',
        "appendonly yes",
        "appendfsync everysec",
    ]
    assert conf.stat().st_mode & 0o077 == 0
    assert service["environment"]["REDISCLI_AUTH"] == "${REDIS_PASSWORD}"
    assert service["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
