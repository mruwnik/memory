"""Tests for the Slack push-events endpoint helpers (slack-changes.md §3.3).

Focuses on the pure-function security primitives that don't need DB or
fakeredis: HMAC verification, signature prefix discipline, signature
basestring construction. Higher-level dispatch and route tests live with
the broader Slack API tests once the DB-backed fixtures are available.
"""

import hashlib
import hmac
from unittest.mock import patch

import pytest

from memory.api.slack import _dispatch_event_callback, _verify_slack_signature


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    """Build a v0= header the way Slack would."""
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_verify_slack_signature_accepts_valid_signature():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    assert _verify_slack_signature(secret, ts, body, sig) is True


def test_verify_slack_signature_rejects_modified_body():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    tampered = b'{"type":"event_callback","extra":"x"}'
    assert _verify_slack_signature(secret, ts, tampered, sig) is False


def test_verify_slack_signature_rejects_modified_ts():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    assert _verify_slack_signature(secret, "1620000001", body, sig) is False


def test_verify_slack_signature_rejects_wrong_secret():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    assert _verify_slack_signature("different-secret", ts, body, sig) is False


@pytest.mark.parametrize(
    "bad_header",
    [
        # Missing v0= prefix entirely. We must reject — the prefix is the
        # only versioning marker, and accepting a bare hex digest invites
        # rolling-secret confusion later.
        "abcdef0123456789",
        # Wrong version marker. v1 is not Slack's current scheme.
        "v1=abcdef",
        # Empty.
        "",
        # Just the prefix with no digest.
        "v0=",
    ],
)
def test_verify_slack_signature_rejects_malformed_header(bad_header):
    assert (
        _verify_slack_signature("secret", "1620000000", b"body", bad_header)
        is False
    )


def test_verify_slack_signature_rejects_wrong_length_digest():
    """A v0= prefix followed by a non-sha256-length digest must fail.

    Without compare_digest's length check this would still be safe, but
    a mismatch should never accidentally match — be explicit about it.
    """
    short_sig = "v0=" + "a" * 10  # 10 chars instead of 64
    assert (
        _verify_slack_signature("secret", "1620000000", b"body", short_sig)
        is False
    )


def test_verify_slack_signature_basestring_includes_v0_prefix():
    """The basestring is `v0:{ts}:{body}`. A signature computed over
    a different basestring (e.g. omitting the v0: prefix) must fail.

    This mutation discriminator catches a future refactor that
    accidentally drops the `v0:` literal."""
    secret = "abc"
    ts = "1620000000"
    body = b"payload"
    # Signature computed over the WRONG basestring (no v0: prefix).
    wrong_basestring = f"{ts}:".encode() + body
    bad_digest = hmac.new(
        secret.encode(), wrong_basestring, hashlib.sha256
    ).hexdigest()
    assert (
        _verify_slack_signature(secret, ts, body, f"v0={bad_digest}") is False
    )


def test_verify_slack_signature_basestring_uses_colon_separator():
    """Mutation discriminator: separator between ts and body is `:`,
    not space, dash, etc. Catches a refactor that munges the basestring
    format."""
    secret = "abc"
    ts = "1620000000"
    body = b"payload"
    wrong_basestring = f"v0:{ts} ".encode() + body  # space instead of colon
    bad_digest = hmac.new(
        secret.encode(), wrong_basestring, hashlib.sha256
    ).hexdigest()
    assert (
        _verify_slack_signature(secret, ts, body, f"v0={bad_digest}") is False
    )


# ---------------------------------------------------------------------------
# Dispatcher routing tests — _dispatch_event_callback
# ---------------------------------------------------------------------------


def _capture_send_task():
    """Patch celery_app.send_task and return the calls list."""
    return patch("memory.api.slack.celery_app.send_task")


# Realistic Slack-shaped IDs satisfying _SLACK_TEAM_ID_PATTERN /
# _SLACK_CHANNEL_ID_PATTERN. The dispatcher validates these as defense in
# depth (HMAC proves authenticity, not content shape), so test events must
# carry well-formed values to reach the task-routing branches.
GOOD_TEAM_ID = "T12345678"
GOOD_CHANNEL_ID = "C12345678"


@pytest.mark.parametrize(
    "event, expected_task_suffix, expected_kwarg_keys",
    [
        # Plain message → ADD_SLACK_MESSAGE
        (
            {"event": {"type": "message", "ts": "1.2", "user": "U1",
                       "text": "hi", "channel": GOOD_CHANNEL_ID},
             "team_id": GOOD_TEAM_ID},
            "add_slack_message",
            {"workspace_id", "channel_id", "message_ts", "author_id", "content",
             "slack_app_id"},
        ),
        # Edited → still ADD_SLACK_MESSAGE; merge logic in worker handles ordering.
        (
            {"event": {"type": "message", "subtype": "message_changed",
                       "channel": GOOD_CHANNEL_ID,
                       "message": {"ts": "1.2", "user": "U1", "text": "edited"}},
             "team_id": GOOD_TEAM_ID},
            "add_slack_message",
            {"workspace_id", "message_ts", "slack_app_id"},
        ),
        # Deleted → MARK_SLACK_MESSAGE_DELETED
        (
            {"event": {"type": "message", "subtype": "message_deleted",
                       "deleted_ts": "1.2", "channel": GOOD_CHANNEL_ID},
             "team_id": GOOD_TEAM_ID},
            "mark_slack_message_deleted",
            {"workspace_id", "channel_id", "message_ts", "slack_app_id"},
        ),
        # Reaction added → UPDATE_SLACK_REACTIONS (channel comes from event.item.channel)
        (
            {"event": {"type": "reaction_added",
                       "item": {"channel": GOOD_CHANNEL_ID, "ts": "1.2"},
                       "reactions": [{"name": "thumbsup", "count": 1}]},
             "team_id": GOOD_TEAM_ID},
            "update_slack_reactions",
            {"workspace_id", "channel_id", "message_ts", "reactions",
             "slack_app_id"},
        ),
        # Reaction removed → UPDATE_SLACK_REACTIONS (same handler as add).
        (
            {"event": {"type": "reaction_removed",
                       "item": {"channel": GOOD_CHANNEL_ID, "ts": "1.2"},
                       "reactions": []},
             "team_id": GOOD_TEAM_ID},
            "update_slack_reactions",
            {"workspace_id", "channel_id", "message_ts", "reactions",
             "slack_app_id"},
        ),
    ],
)
def test_dispatch_event_callback_routes_correctly(
    event, expected_task_suffix, expected_kwarg_keys
):
    with _capture_send_task() as mock_send:
        result = _dispatch_event_callback(event, slack_app_id=42)

    assert mock_send.called
    task_name, kwargs = mock_send.call_args.args[0], mock_send.call_args.kwargs["kwargs"]
    assert task_name.endswith(expected_task_suffix)
    assert expected_kwarg_keys.issubset(kwargs.keys())
    assert kwargs["slack_app_id"] == 42
    assert result["dispatch"]


def test_dispatch_event_callback_ignores_unknown_event_type():
    """A future Slack event type we don't recognize must not crash and must
    not enqueue a celery task — better to drop than to misroute.

    Note: the team/channel validation runs before the type dispatch, so we
    pass a well-formed channel even though it's never used."""
    with _capture_send_task() as mock_send:
        result = _dispatch_event_callback(
            {"event": {"type": "team_renamed", "channel": GOOD_CHANNEL_ID},
             "team_id": GOOD_TEAM_ID},
            slack_app_id=1,
        )
    mock_send.assert_not_called()
    assert result["dispatch"] == "ignored"


def test_dispatch_event_callback_message_changed_uses_inner_message_block():
    """`message_changed` events nest the actual message under
    `event.message`. The dispatcher must read fields from that nested
    object, not the outer event (which has type=message but may not
    carry the post-edit ts/text)."""
    with _capture_send_task() as mock_send:
        _dispatch_event_callback(
            {
                "event": {
                    "type": "message",
                    "subtype": "message_changed",
                    "channel": GOOD_CHANNEL_ID,
                    "message": {
                        "ts": "1.5",
                        "user": "U1",
                        "text": "the edited content",
                        "edited": {"ts": "1.6"},
                    },
                },
                "team_id": GOOD_TEAM_ID,
            },
            slack_app_id=7,
        )
    kwargs = mock_send.call_args.kwargs["kwargs"]
    assert kwargs["message_ts"] == "1.5"
    assert kwargs["content"] == "the edited content"
    assert kwargs["edited_ts"] == "1.6"
    # subtype is preserved so the merge logic knows this is an edit event.
    assert kwargs["subtype"] == "message_changed"


# ---------------------------------------------------------------------------
# Defense-in-depth: malformed team_id / channel_id rejected pre-dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "team_id_value, expected_dispatch",
    [
        (None, "rejected_team_id"),
        ("", "rejected_team_id"),
        ("T1", "rejected_team_id"),  # too short
        ("X12345678", "rejected_team_id"),  # wrong prefix
        ("T123 45678", "rejected_team_id"),  # whitespace
        ("T<script>alert(1)</script>", "rejected_team_id"),  # XSS payload
    ],
)
def test_dispatch_event_callback_rejects_malformed_team_id(
    team_id_value, expected_dispatch
):
    """The HMAC verifies authenticity-from-Slack but not content shape.
    Garbage team_id flowing into Celery kwargs would land as junk in
    SlackMessage.workspace_id (Text col, no DB shape constraint)."""
    with _capture_send_task() as mock_send:
        result = _dispatch_event_callback(
            {"event": {"type": "message", "ts": "1.0",
                       "channel": GOOD_CHANNEL_ID},
             "team_id": team_id_value},
            slack_app_id=1,
        )
    mock_send.assert_not_called()
    assert result["dispatch"] == expected_dispatch


@pytest.mark.parametrize(
    "channel_id_value, expected_dispatch",
    [
        (None, "rejected_channel_id"),
        ("", "rejected_channel_id"),
        ("C1", "rejected_channel_id"),  # too short
        ("Z12345678", "rejected_channel_id"),  # wrong prefix
        ("C123;DROP TABLE", "rejected_channel_id"),  # SQL-shape garbage
    ],
)
def test_dispatch_event_callback_rejects_malformed_channel_id(
    channel_id_value, expected_dispatch
):
    """Same defense-in-depth rationale as the team_id test, applied to
    channel_id (event.channel for messages, event.item.channel for
    reactions)."""
    with _capture_send_task() as mock_send:
        result = _dispatch_event_callback(
            {"event": {"type": "message", "ts": "1.0",
                       "channel": channel_id_value},
             "team_id": GOOD_TEAM_ID},
            slack_app_id=1,
        )
    mock_send.assert_not_called()
    assert result["dispatch"] == expected_dispatch


def test_dispatch_event_callback_accepts_dict_channel_with_valid_id():
    """channel_* events deliver `event.channel` as a full channel object
    (dict). The dispatcher must extract `id` and validate that, not the
    outer dict literal."""
    with _capture_send_task() as mock_send:
        result = _dispatch_event_callback(
            {
                "event": {
                    "type": "channel_rename",
                    "channel": {"id": GOOD_CHANNEL_ID, "name": "renamed"},
                },
                "team_id": GOOD_TEAM_ID,
            },
            slack_app_id=3,
        )
    assert result["dispatch"] == "update_channel"
    kwargs = mock_send.call_args.kwargs["kwargs"]
    assert kwargs["channel_id"] == GOOD_CHANNEL_ID
    # Full dict ships as channel_payload for downstream merging.
    assert kwargs["channel_payload"]["name"] == "renamed"
