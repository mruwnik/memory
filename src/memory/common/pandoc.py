"""Running pandoc on untrusted documents."""

import logging
import os
import resource
import signal
import subprocess
from typing import Sequence

from memory.common import settings

logger = logging.getLogger(__name__)

# Keep reading and writing to the files named on the command line (no fetching
# of linked images or other resources), and run TeX with no shell escape.
UNTRUSTED_INPUT_ARGS = ("--sandbox",)
PDF_ENGINE_ARGS = ("--pdf-engine=xelatex", "--pdf-engine-opt=-no-shell-escape")


class PandocError(RuntimeError):
    """pandoc failed or ran out of time. The message never quotes pandoc's
    output, which can carry document text."""


def kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def limit_memory() -> None:
    """Cap the address space of the process about to exec pandoc; xelatex and
    the other processes it starts inherit the cap. Runs in the forked child."""
    limit = settings.PANDOC_MEMORY_LIMIT_BYTES
    if limit > 0:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run_pandoc(pandoc: str, args: Sequence[str]) -> bytes:
    """Run ``pandoc`` with ``args`` and return what it wrote to stdout.

    pandoc starts in its own session, so on a timeout of
    PANDOC_TIMEOUT_SECONDS it is killed together with every process it
    started (xelatex and its helpers). Each of those processes may map at
    most PANDOC_MEMORY_LIMIT_BYTES.
    """
    timeout = settings.PANDOC_TIMEOUT_SECONDS
    with subprocess.Popen(
        [pandoc, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        preexec_fn=limit_memory,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(process.pid)
            process.communicate()
            raise PandocError(f"pandoc ran longer than {timeout} seconds")
    if process.returncode != 0:
        logger.debug("pandoc stderr: %s", stderr[-2000:].decode("utf-8", "replace"))
        raise PandocError(f"pandoc exited with status {process.returncode}")
    return stdout
