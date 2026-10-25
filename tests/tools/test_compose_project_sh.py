import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).parents[2] / "tools" / "compose_project.sh"


@pytest.fixture
def checkout(tmp_path):
    """tmp_path/app, reachable through the symlink tmp_path/memory."""
    (tmp_path / "app").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "memory").symlink_to(tmp_path / "app")
    return tmp_path / "app"


def project(tmp_path, docker_bin, via, containers=(), volumes=()):
    paths = {"app": tmp_path / "app", "link": tmp_path / "memory", "elsewhere": tmp_path / "elsewhere"}
    (tmp_path / "containers").write_text("".join(f"{c.format(**paths)}\n" for c in containers))
    (tmp_path / "volumes").write_text("".join(f"{v}\n" for v in volumes))
    result = subprocess.run(
        ["bash", str(SCRIPT), via.format(**paths)],
        env={"PATH": f"{docker_bin}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip(), result.stderr


@pytest.mark.parametrize(
    "containers, volumes, via, expected",
    [
        (["memory {link}", "memory {link}"], ["memory db_data"], "{app}", "memory"),
        (["chris {app}"], ["chris db_data", "memory db_data"], "{link}", "chris"),
        (["memory {link}", "other /nonexistent", "other {elsewhere}"], [], "{app}", "memory"),
        (["memory {link}"], ["other cache"], "{app}", "memory"),
    ],
    ids=["via-link", "under-p-beside-another-stack", "ignores-other-stacks", "no-db-volumes-yet"],
)
def test_names_the_project_running_from_the_checkout(
    tmp_path, checkout, docker_bin, containers, volumes, via, expected
):
    code, name, err = project(tmp_path, docker_bin, via, containers, volumes)

    assert (code, name) == (0, expected), err


@pytest.mark.parametrize(
    "containers, volumes, listed",
    [
        (["memory {app}", "app {app}"], ["memory db_data"], "memory_db_data"),
        ([], ["memory db_data"], "memory_db_data"),
        ([], [], "none"),
        (["chris {elsewhere}"], ["chris db_data"], "chris_db_data"),
        (["app {app}"], ["memory db_data", "app redis_data"], "memory_db_data"),
    ],
    ids=["two-running", "stack-down", "nothing", "p-with-working-dir-elsewhere", "stray-app"],
)
def test_refuses_to_guess(tmp_path, checkout, docker_bin, containers, volumes, listed):
    code, name, err = project(tmp_path, docker_bin, "{app}", containers, volumes)

    assert code != 0
    assert name == ""
    assert f"db_data volumes: {listed}" in err
    assert "Set COMPOSE_PROJECT_NAME in .env" in err


def test_a_failing_docker_is_reported(tmp_path, checkout, docker_bin):
    (tmp_path / "docker.fails").write_text("")

    code, name, err = project(tmp_path, docker_bin, "{app}")

    assert code != 0
    assert "Cannot connect to the Docker daemon" in err
    assert "Set COMPOSE_PROJECT_NAME in .env" in err
