import os
import pathlib
import stat
import time

import pytest

from memory.common import extract, settings
from memory.common.pandoc import PandocError
from tests.memory.office_files import bomb_docx

SECRET = "confidential-merger-plan"


def write_script(path: pathlib.Path, body: str) -> str:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def docx(tmp_path) -> pathlib.Path:
    path = tmp_path / "in.docx"
    path.write_bytes(b"not a zip, so not measured")
    return path


@pytest.fixture
def docx_bomb(tmp_path) -> pathlib.Path:
    path = tmp_path / "bomb.docx"
    path.write_bytes(bomb_docx(8 * 1024 * 1024))
    return path


@pytest.fixture
def recording_pandoc(tmp_path, monkeypatch) -> pathlib.Path:
    """A pandoc that writes its arguments, one per line, to the returned file."""
    record = tmp_path / "args"
    script = write_script(tmp_path / "pandoc", f'printf "%s\\n" "$@" > {record}; echo text')
    monkeypatch.setattr(extract, "PANDOC_PATH", script)
    return record


def process_gone(pid: int) -> bool:
    """True once ``pid`` has exited (a zombie counts), waiting up to 5 s."""
    stat_file = pathlib.Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not stat_file.exists() or stat_file.read_text().split()[2] in {"Z", "X"}:
            return True
        time.sleep(0.05)
    return False


def test_docx_to_pdf_runs_tex_without_shell_escape(recording_pandoc, docx, tmp_path):
    extract.docx_to_pdf(docx, tmp_path / "out.pdf")

    args = recording_pandoc.read_text().splitlines()
    assert {"--pdf-engine=xelatex", "--pdf-engine-opt=-no-shell-escape", "--sandbox"} <= set(args)
    assert args[-2:] == ["--", str(docx)]


def test_extract_docx_text_runs_sandboxed(recording_pandoc, docx):
    chunks = extract.extract_docx_text(docx)

    args = recording_pandoc.read_text().splitlines()
    assert "--sandbox" in args
    assert args[-2:] == ["--", str(docx)]
    assert [c.data for c in chunks] == [["text"]]


def test_docx_to_pdf_kills_pandoc_and_its_children_on_timeout(
    tmp_path, monkeypatch, docx
):
    pid_file = tmp_path / "child.pid"
    script = write_script(
        tmp_path / "pandoc", f"sleep 60 & echo $! > {pid_file}; sleep 60"
    )
    monkeypatch.setattr(extract, "PANDOC_PATH", script)
    monkeypatch.setattr(settings, "PANDOC_TIMEOUT_SECONDS", 0.5)

    started = time.monotonic()
    with pytest.raises(PandocError):
        extract.docx_to_pdf(docx, tmp_path / "out.pdf")

    assert time.monotonic() - started < 10
    assert process_gone(int(pid_file.read_text()))


def test_failed_pandoc_error_does_not_quote_its_output(tmp_path, monkeypatch, docx):
    script = write_script(tmp_path / "pandoc", f"echo '! LaTeX Error. {SECRET}' >&2; exit 43")
    monkeypatch.setattr(extract, "PANDOC_PATH", script)

    with pytest.raises(PandocError) as error:
        extract.extract_docx_text(docx)

    assert "43" in str(error.value)
    assert SECRET not in str(error.value)


def test_extract_docx_refuses_a_bomb_before_pandoc(
    recording_pandoc, monkeypatch, tmp_path, docx_bomb
):
    monkeypatch.setattr(settings, "MAX_OFFICE_MARKUP_BYTES", 1024 * 1024)

    with pytest.raises(ValueError):
        extract.extract_docx(docx_bomb)

    assert not recording_pandoc.exists()
    assert os.path.getsize(docx_bomb) < 64 * 1024


def test_pandoc_runs_under_the_memory_limit(tmp_path, monkeypatch, docx):
    script = write_script(tmp_path / "pandoc", "ulimit -v")
    monkeypatch.setattr(extract, "PANDOC_PATH", script)
    monkeypatch.setattr(settings, "PANDOC_MEMORY_LIMIT_BYTES", 512 * 1024 * 1024)

    chunks = extract.extract_docx_text(docx)

    assert [c.data for c in chunks] == [[str(512 * 1024)]]
