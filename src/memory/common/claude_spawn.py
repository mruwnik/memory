"""Spawn-request checks shared by interactive and scheduled Claude sessions."""

import re


def check_dev_channels_server(value: str | None) -> str | None:
    """Restrict to characters valid for a hostname, host:port, or URL.

    The value is passed verbatim to the orchestrator, which appends it to
    the claude command line. Allow only an unambiguous charset so it can't
    carry shell metacharacters or whitespace, and forbid a leading '-' so
    it can never be mistaken for a flag (no hostname/host:port/URL starts
    with one).
    """
    if value is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:/][A-Za-z0-9._:/-]*", value):
        raise ValueError(
            "dev_channels_server may only contain letters, digits, and "
            ". _ : / - and may not start with '-'"
        )
    return value
