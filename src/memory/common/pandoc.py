"""Running pandoc on untrusted documents."""

import logging
import os
import resource
import signal
import subprocess
import sys
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
    """Cap the data memory of the process about to exec pandoc; xelatex and
    the other processes it starts inherit the cap. Runs in the forked child.

    RLIMIT_DATA, not RLIMIT_AS: pandoc's GHC runtime reserves a large heap
    address range up front with a PROT_NONE mapping, which RLIMIT_AS counts.
    Since Linux 4.7 RLIMIT_DATA counts private writable mappings only, so it
    bounds the memory actually used without refusing that reservation.
    macOS refuses an RLIMIT_DATA below what is already mapped, so there
    pandoc runs uncapped.
    """
    limit = settings.PANDOC_MEMORY_LIMIT_BYTES
    if limit > 0 and sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))


def run_pandoc(pandoc: str, args: Sequence[str]) -> bytes:
    """Run ``pandoc`` with ``args`` and return what it wrote to stdout.

    pandoc starts in its own session, so on a timeout of
    PANDOC_TIMEOUT_SECONDS it is killed together with every process it
    started (xelatex and its helpers). Each of those processes may use at
    most PANDOC_MEMORY_LIMIT_BYTES of data memory.
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
