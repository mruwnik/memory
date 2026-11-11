"""Running pandoc on untrusted documents."""

import logging
import os
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


def run_pandoc(pandoc: str, args: Sequence[str]) -> bytes:
    """Run ``pandoc`` with ``args`` and return what it wrote to stdout.

    pandoc starts in its own session, so on a timeout of
    PANDOC_TIMEOUT_SECONDS it is killed together with every process it
    started (xelatex and its helpers).
    """
    timeout = settings.PANDOC_TIMEOUT_SECONDS
    with subprocess.Popen(
        [pandoc, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
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
