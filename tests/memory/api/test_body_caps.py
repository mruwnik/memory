"""Which body cap each route gets."""

import pytest

from memory.api.body_limit import body_cap_for
from memory.common import ingest_routing, settings


@pytest.mark.parametrize(
    "path, cap",
    [
        ("/api/polls/respond/some-poll", lambda: settings.PUBLIC_REQUEST_BODY_MAX_BYTES),
        ("/auth/password-reset/request", lambda: settings.PUBLIC_REQUEST_BODY_MAX_BYTES),
        ("/oauth/login", lambda: settings.PUBLIC_REQUEST_BODY_MAX_BYTES),
        ("/token", lambda: settings.PUBLIC_REQUEST_BODY_MAX_BYTES),
        ("/register", lambda: settings.PUBLIC_REQUEST_BODY_MAX_BYTES),
        ("/setup/claim", lambda: settings.PUBLIC_REQUEST_BODY_MAX_BYTES),
        ("/mcp", lambda: settings.REQUEST_BODY_MAX_BYTES),
        ("/mcp/", lambda: settings.REQUEST_BODY_MAX_BYTES),
        ("/sessions/ingest/batch", lambda: settings.REQUEST_BODY_MAX_BYTES),
        ("/github/webhook/3", lambda: settings.GITHUB_WEBHOOK_MAX_BODY_BYTES),
        ("/slack/events/A1", lambda: settings.WEBHOOK_MAX_BODY_BYTES),
        ("/discord/events/9", lambda: settings.WEBHOOK_MAX_BODY_BYTES),
        ("/transcripts/ingest/4", lambda: settings.WEBHOOK_MAX_BODY_BYTES),
        ("/ingest/upload", ingest_routing.max_ingest_bytes),
        ("/books/upload", lambda: settings.MAX_BOOK_UPLOAD_BYTES + 1024 * 1024),
        # A sibling sharing a prefix gets no upload cap.
        ("/ingest/uploads", lambda: settings.REQUEST_BODY_MAX_BYTES),
    ],
)
def test_body_cap_for(path, cap):
    assert body_cap_for(path) == cap()
