import re
from datetime import datetime, timezone

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

SYNC_ERROR_MAX_LEN = 1000

SECRET_PATTERNS = [
    # Greedy to the last "@" so passwords containing "/" or "@" are covered.
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s\"'<>]*@", re.I), r"\1***@"),
    (
        re.compile(r"(Authorization[\"']?\s*:\s*[\"']?(?:Bearer|Basic|token)\s+)[^\s\"',}]+", re.I),
        r"\1***",
    ),
    (re.compile(r"\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 ***"),
    (re.compile(r"\b(Basic)\s+[A-Za-z0-9+/]{16,}={0,2}"), r"\1 ***"),
    (
        re.compile(
            r"((?:token|secret|password|passwd|key|signature|credential)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+",
            re.I,
        ),
        r"\1***",
    ),
    (re.compile(r"([?&](?:code|sig)=)[^&\s\"'>]+", re.I), r"\1***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?"), "***"),
    (
        re.compile(
            r"\b(?:gh[pousr]_|github_pat_|xox[abeposr]-|sk-|ya29\.|AIza|AKIA)[A-Za-z0-9._/-]+"
        ),
        "***",
    ),
]


def redact_secrets(text: str) -> str:
    """Mask credentials that libraries embed in error messages (URLs, auth headers, tokens)."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SyncStatusMixin:
    """Outcome of a source's most recent sync, so persistently failing sources can be flagged.

    `sync_failing_since` is the time of the first failure in the current streak;
    a successful sync clears both columns.
    """

    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_failing_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def record_sync(self, error: BaseException | str | None) -> None:
        if error is None:
            self.sync_error = None
            self.sync_failing_since = None
            return
        # Stored redacted because it is shown to users verbatim.
        self.sync_error = redact_secrets(str(error))[:SYNC_ERROR_MAX_LEN]
        self.sync_failing_since = self.sync_failing_since or datetime.now(timezone.utc)

