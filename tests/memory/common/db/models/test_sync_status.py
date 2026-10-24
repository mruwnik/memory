from datetime import datetime, timezone

import pytest

from memory.api.sync_status import sync_status_fields
from memory.common.db.models.sources import GoogleFolder
from memory.common.db.models.sync_status import SYNC_ERROR_MAX_LEN, redact_secrets


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "<HttpError 404 when requesting https://www.googleapis.com/drive/v3/files/abc returned \"File not found\">",
            "<HttpError 404 when requesting https://www.googleapis.com/drive/v3/files/abc returned \"File not found\">",
        ),
        ("cannot connect to imaps://bob:hunter2@mail.example.com:993", "cannot connect to imaps://***@mail.example.com:993"),
        ("GET https://feed.example.com/rss?user=1&token=abc123&x=2 failed", "GET https://feed.example.com/rss?user=1&token=***&x=2 failed"),
        ("401 for https://api.example.com/?access_token=s3cr3t", "401 for https://api.example.com/?access_token=***"),
        ("headers: Authorization: Bearer eyJhbGciOi.payload.sig", "headers: Authorization: Bearer ***"),
        ("bad credentials ghp_abcdefghijklmnopqrstuvwxyz0123456789", "bad credentials ***"),
        ("invalid_auth for xoxb-1234-5678-abcdef", "invalid_auth for ***"),
        ("xoxe-1-My0xLTEyMzQ refresh failed", "*** refresh failed"),
        ("https://user:p@ss/word@host/x", "https://***@host/x"),
        (
            "https://x/y?client_id=a&client_secret=s3cr3t&refresh_token=1//0abc",
            "https://x/y?client_id=a&client_secret=***&refresh_token=***",
        ),
        ("Authorization: token abcdef1234567890abcdef", "Authorization: token ***"),
        ("{'access_token': 'ya29.a0AfH6', 'refresh_token': '1//0gLx-abc'}", "{'access_token': '***', 'refresh_token': '***'}"),
        ('{"error":"invalid_grant","refresh_token":"1//0gLxyz"}', '{"error":"invalid_grant","refresh_token":"***"}'),
        ("password=hunter2 in body", "password=*** in body"),
        ("eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOjF9.abc JWT rejected", "*** JWT rejected"),
        ("X-Amz-Signature=deadbeef&X-Amz-Credential=AKIAXXXX", "X-Amz-Signature=***&X-Amz-Credential=***"),
        ("https://x/cb?code=4/0AbCd&state=s", "https://x/cb?code=***&state=s"),
        ("Basic authentication failed for user", "Basic authentication failed for user"),
        ("Basic dXNlcjpodW50ZXIyMTIzNDU2", "Basic ***"),
        ("Token invalid: invalid_auth", "Token invalid: invalid_auth"),
    ],
)
def test_redact_secrets(text, expected):
    assert redact_secrets(text) == expected


def test_record_sync_tracks_failure_streak_until_success():
    folder = GoogleFolder(folder_id="f1", folder_name="Folder")

    folder.record_sync(Exception("404 not found"))
    first_since = folder.sync_failing_since
    folder.record_sync("still 404 https://x:pw@example.com")

    assert isinstance(first_since, datetime)
    assert first_since.tzinfo == timezone.utc
    assert folder.sync_failing_since == first_since
    assert folder.sync_error == "still 404 https://***@example.com"

    folder.record_sync(None)

    assert (folder.sync_error, folder.sync_failing_since) == (None, None)


def test_record_sync_truncates_long_errors():
    folder = GoogleFolder(folder_id="f1", folder_name="Folder")

    folder.record_sync("x" * (SYNC_ERROR_MAX_LEN * 2))

    assert folder.sync_error == "x" * SYNC_ERROR_MAX_LEN


def test_sync_status_fields_redacts_errors_stored_unredacted():
    folder = GoogleFolder(folder_id="f1", folder_name="Folder", sync_error="https://bob:pw@host failed")

    assert sync_status_fields(folder) == {"sync_error": "https://***@host failed", "sync_failing_since": None}
