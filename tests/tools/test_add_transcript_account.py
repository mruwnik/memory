"""Tests for the add_transcript_account CLI."""

from unittest.mock import patch

import pytest

from memory.workers.tasks.transcripts import PROVIDERS
from tools import add_transcript_account


def test_cli_provider_choices_match_worker_providers():
    """The CLI's --provider choices must be derived from the worker's
    PROVIDERS dict so the two can never drift. A user must not be able to
    create an account for a provider the worker doesn't know about (it would
    fail every sync forever with `unsupported provider:`)."""
    # Each known worker provider should be accepted by the CLI parser.
    for provider in PROVIDERS:
        argv = [
            "add_transcript_account.py",
            "--user-email",
            "x@example.com",
            "--name",
            "n",
            "--provider",
            provider,
            "--api-key",
            "k",
        ]
        with patch("sys.argv", argv):
            ns = add_transcript_account.parse_args()
        assert ns.provider == provider


def test_cli_rejects_unknown_provider():
    """A provider name not in PROVIDERS must be rejected by argparse before
    the worker ever sees the value (catches CLI/worker drift at parse time)."""
    argv = [
        "add_transcript_account.py",
        "--user-email",
        "x@example.com",
        "--name",
        "n",
        "--provider",
        "definitely-not-a-real-provider",
        "--api-key",
        "k",
    ]
    with patch("sys.argv", argv):
        with pytest.raises(SystemExit):
            add_transcript_account.parse_args()
