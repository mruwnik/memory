"""Tests for the Slack push-events endpoint helpers (slack-changes.md §3.3).

Focuses on the pure-function security primitives that don't need DB or
fakeredis: HMAC verification, signature prefix discipline, signature
basestring construction. Higher-level dispatch and route tests live with
the broader Slack API tests once the DB-backed fixtures are available.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import update

from memory.api import slack as slack_api
from memory.api.slack import SlackEnvelope, _verify_slack_signature, plan_event_callback
from memory.common.db.models.slack import SlackApp, SlackChannel, SlackUserCredentials, SlackWorkspace
from memory.common.encryption import encrypt_value
from tests.conftest import raw_events


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
# Dispatcher routing tests — plan_event_callback
# ---------------------------------------------------------------------------


def plan(body: dict, slack_app_id: int):
    return plan_event_callback(SlackEnvelope.model_validate(body), slack_app_id)


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
            {"event": {"type": "reaction_added", "user": "U1", "reaction": "thumbsup",
                       "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": "1.2"}},
             "team_id": GOOD_TEAM_ID},
            "update_slack_reactions",
            {"workspace_id", "channel_id", "message_ts", "emoji", "user_id", "action",
             "slack_app_id"},
        ),
        # Reaction removed → UPDATE_SLACK_REACTIONS (same handler as add).
        (
            {"event": {"type": "reaction_removed", "user": "U1", "reaction": "thumbsup",
                       "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": "1.2"}},
             "team_id": GOOD_TEAM_ID},
            "update_slack_reactions",
            {"workspace_id", "channel_id", "message_ts", "emoji", "user_id", "action",
             "slack_app_id"},
        ),
    ],
)
def test_dispatch_event_callback_routes_correctly(
    event, expected_task_suffix, expected_kwarg_keys
):
    label, task_name, kwargs = plan(event, slack_app_id=42)

    assert task_name is not None
    assert task_name.endswith(expected_task_suffix)
    assert expected_kwarg_keys.issubset(kwargs.keys())
    assert kwargs["slack_app_id"] == 42
    assert label


@pytest.mark.parametrize(
    "event_type, action", [("reaction_added", "add"), ("reaction_removed", "remove")]
)
def test_dispatch_reaction_event_passes_who_reacted_with_what(event_type, action):
    _, _, kwargs = plan(
        {"event": {"type": event_type, "user": "U777", "reaction": "tada",
                   "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": "5.5"}},
         "team_id": GOOD_TEAM_ID},
        slack_app_id=9,
    )

    assert (kwargs["message_ts"], kwargs["emoji"], kwargs["user_id"], kwargs["action"]) == (
        "5.5", "tada", "U777", action,
    )


def test_dispatch_ignores_reactions_on_files():
    label, task, kwargs = plan(
        {"event": {"type": "reaction_added", "user": "U1", "reaction": "tada",
                   "item": {"type": "file", "file": "F123"}},
         "team_id": GOOD_TEAM_ID},
        slack_app_id=9,
    )

    assert task is None
    assert label.startswith("rejected")


def test_dispatch_event_callback_ignores_unknown_event_type():
    """A future Slack event type we don't recognize must not crash and must
    not enqueue a celery task — better to drop than to misroute.

    Note: the team/channel validation runs before the type dispatch, so we
    pass a well-formed channel even though it's never used."""
    label, task, kwargs = plan(
        {"event": {"type": "team_renamed", "channel": GOOD_CHANNEL_ID},
         "team_id": GOOD_TEAM_ID},
        slack_app_id=1,
    )
    assert task is None
    assert label == "ignored"


def test_dispatch_event_callback_message_changed_uses_inner_message_block():
    """`message_changed` events nest the actual message under
    `event.message`. The dispatcher must read fields from that nested
    object, not the outer event (which has type=message but may not
    carry the post-edit ts/text)."""
    _, _, kwargs = plan(
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
    label, task, kwargs = plan(
        {"event": {"type": "message", "ts": "1.0",
                   "channel": GOOD_CHANNEL_ID},
         "team_id": team_id_value},
        slack_app_id=1,
    )
    assert task is None
    assert label == expected_dispatch


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
    label, task, kwargs = plan(
        {"event": {"type": "message", "ts": "1.0",
                   "channel": channel_id_value},
         "team_id": GOOD_TEAM_ID},
        slack_app_id=1,
    )
    assert task is None
    assert label == expected_dispatch


def malformed_ts_message(**fields) -> dict:
    return {"type": "message", "ts": "1704067200.000100", "user": "U1", "channel": GOOD_CHANNEL_ID} | fields


def malformed_ts_reaction(ts: str) -> dict:
    return {"type": "reaction_added", "user": "U1", "reaction": "tada",
            "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": ts}}


@pytest.mark.parametrize(
    "event",
    [
        malformed_ts_message(ts="/var/tmp/x"),
        malformed_ts_message(ts=None),
        malformed_ts_message(ts="1704067200"),
        malformed_ts_message(ts="1704067200.000100\n"),
        malformed_ts_message(ts="1704067200.000100/../x"),
        malformed_ts_message(ts="\u0661\u0667\u0660\u0664\u0660\u0666\u0667\u0662\u0660\u0660.\u0661"),
        malformed_ts_message(thread_ts="/srv/x"),
        malformed_ts_message(edited={"ts": "../x"}),
        malformed_ts_message(subtype="message_changed", message={"ts": "/var/tmp/x", "user": "U1"}),
        malformed_ts_message(subtype="message_deleted", ts=None, deleted_ts="/var/tmp/x"),
        malformed_ts_reaction("abc"),
    ],
)
def test_dispatch_event_callback_rejects_malformed_ts(event):
    """A message timestamp names a directory on disk, so only Slack's digits.digits form is dispatched."""
    label, task, kwargs = plan({"event": event, "team_id": GOOD_TEAM_ID}, slack_app_id=1)

    assert (label, task, kwargs) == ("rejected_ts", None, {})


def test_dispatch_event_callback_accepts_dict_channel_with_valid_id():
    """channel_* events deliver `event.channel` as a full channel object
    (dict). The dispatcher must extract `id` and validate that, not the
    outer dict literal."""
    label, task, kwargs = plan(
        {
            "event": {
                "type": "channel_rename",
                "channel": {"id": GOOD_CHANNEL_ID, "name": "renamed"},
            },
            "team_id": GOOD_TEAM_ID,
        },
        slack_app_id=3,
    )
    assert label == "update_channel"
    assert kwargs["channel_id"] == GOOD_CHANNEL_ID
    # Full dict ships as channel_payload for downstream merging.
    assert kwargs["channel_payload"]["name"] == "renamed"


@pytest.mark.parametrize(
    "event_type, payload",
    [("channel_archive", {"is_archived": True}), ("channel_unarchive", {"is_archived": False})],
)
def test_dispatch_archive_events_carry_only_the_archived_flag(event_type, payload):
    label, task, kwargs = plan(
        {"event": {"type": event_type, "channel": GOOD_CHANNEL_ID, "user": "U1"}, "team_id": GOOD_TEAM_ID},
        slack_app_id=3,
    )

    assert (label, kwargs["channel_payload"]) == ("update_channel", payload)


@pytest.mark.parametrize("event_type", ["channel_deleted", "channel_left", "channel_history_changed"])
def test_dispatch_ignores_channel_events_that_carry_no_metadata(event_type):
    label, task, kwargs = plan(
        {"event": {"type": event_type, "channel": GOOD_CHANNEL_ID}, "team_id": GOOD_TEAM_ID},
        slack_app_id=3,
    )

    assert task is None
    assert label == "ignored"


# ---------------------------------------------------------------------------
# url_verification on the route
# ---------------------------------------------------------------------------

SIGNING_SECRET = "route-signing-secret"


@pytest.fixture
def events_app(db_session, user):
    app = SlackApp(client_id="events.app", name="Events", created_by_user_id=user.id)
    app.signing_secret = SIGNING_SECRET
    db_session.add(app)
    db_session.commit()
    return app


def post_signed(client, path: str, envelope: dict, secret: str = SIGNING_SECRET):
    body = json.dumps(envelope).encode()
    ts = str(int(time.time()))
    return client.post(
        path,
        content=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": _slack_sig(secret, ts, body),
            "content-type": "application/json",
        },
    )


def challenge(value: str) -> dict:
    return {"type": "url_verification", "token": "", "challenge": value}


@pytest.mark.parametrize(
    "state, after",
    [
        ("draft", "signing_verified"),
        ("signing_verified", "signing_verified"),
        ("live", "live"),
        ("degraded", "degraded"),
    ],
)
def test_url_verification_without_nonce_is_verified_by_the_signature(
    client, db_session, events_app, state, after
):
    events_app.setup_state = state
    db_session.commit()

    response = post_signed(client, f"/slack/events/{events_app.id}", challenge(f"no-nonce-{state}"))

    assert (response.status_code, response.content) == (200, f"no-nonce-{state}".encode())
    db_session.refresh(events_app)
    assert events_app.setup_state == after


@pytest.mark.parametrize("state", ["live", "draft"])
def test_url_verification_without_nonce_still_checks_the_signature(
    client, db_session, events_app, state
):
    events_app.setup_state = state
    db_session.commit()

    response = post_signed(
        client, f"/slack/events/{events_app.id}", challenge(f"no-nonce-bad-sig-{state}"), secret="wrong"
    )

    assert response.status_code == 401


def test_url_verification_with_wizard_nonce_advances_a_draft(client, db_session, events_app):
    slack_api._set_wizard_nonce(events_app.id, "nonce-abc", 60)

    response = post_signed(
        client, f"/slack/events/{events_app.id}?wizard_nonce=nonce-abc", challenge("wiz-1")
    )

    assert (response.status_code, response.content) == (200, b"wiz-1")
    db_session.refresh(events_app)
    assert events_app.setup_state == "signing_verified"


@pytest.mark.parametrize("state", ["draft", "live"])
def test_url_verification_with_a_wrong_nonce_is_rejected(client, db_session, events_app, state):
    events_app.setup_state = state
    db_session.commit()
    slack_api._set_wizard_nonce(events_app.id, "nonce-abc", 60)

    response = post_signed(
        client, f"/slack/events/{events_app.id}?wizard_nonce=nonce-xyz", challenge(f"wiz-wrong-{state}")
    )

    assert response.status_code == 401


def message_event(team_id: str, ts: str) -> dict:
    return {
        "type": "event_callback",
        "team_id": team_id,
        "event": {"type": "message", "ts": ts, "user": "U1", "text": "x", "channel": GOOD_CHANNEL_ID},
    }


def test_events_for_a_team_the_app_has_no_credential_in_are_dropped(client, db_session, events_app):
    with _capture_send_task() as send:
        response = post_signed(client, f"/slack/events/{events_app.id}", message_event(GOOD_TEAM_ID, "7.1"))

    assert response.status_code == 200
    send.assert_not_called()


def test_events_for_a_team_the_app_is_connected_to_are_dispatched(client, db_session, events_app, user):
    connect_workspace(db_session, events_app, user)

    with _capture_send_task() as send:
        post_signed(client, f"/slack/events/{events_app.id}", message_event(GOOD_TEAM_ID, "7.2"))

    assert send.call_args.kwargs["kwargs"]["workspace_id"] == GOOD_TEAM_ID


def test_events_for_another_apps_team_are_dropped(client, db_session, events_app, user):
    other = SlackApp(client_id="other.app", name="Other", created_by_user_id=user.id)
    db_session.add_all([other, SlackWorkspace(id=GOOD_TEAM_ID, name="W")])
    db_session.flush()
    db_session.add(SlackUserCredentials(slack_app_id=other.id, workspace_id=GOOD_TEAM_ID, user_id=user.id))
    db_session.commit()

    with _capture_send_task() as send:
        post_signed(client, f"/slack/events/{events_app.id}", message_event(GOOD_TEAM_ID, "7.3"))

    send.assert_not_called()


@pytest.mark.parametrize(
    "state, after",
    [
        ("draft", "signing_verified"),
        ("signing_verified", "signing_verified"),
        ("live", "live"),
        ("degraded", "degraded"),
    ],
)
def test_a_signed_event_for_an_unconnected_workspace_verifies_the_signing_secret(
    client, db_session, events_app, state, after
):
    events_app.setup_state = state
    db_session.commit()

    with _capture_send_task():
        post_signed(client, f"/slack/events/{events_app.id}", message_event(GOOD_TEAM_ID, "8.1"))

    db_session.refresh(events_app)
    assert events_app.setup_state == after


def connect_workspace(db_session, app, user, collect=True):
    db_session.add(SlackWorkspace(id=GOOD_TEAM_ID, name="W", collect_messages=collect))
    db_session.flush()
    db_session.add(SlackUserCredentials(slack_app_id=app.id, workspace_id=GOOD_TEAM_ID, user_id=user.id))
    db_session.commit()


def event_callback(event: dict) -> dict:
    return {"type": "event_callback", "team_id": GOOD_TEAM_ID, "event": event}


@pytest.mark.parametrize(
    "state, after, ts",
    [
        ("draft", "live", "9.1"),
        ("signing_verified", "live", "9.2"),
        ("live", "live", "9.3"),
        ("degraded", "degraded", "9.4"),
    ],
)
# A fresh workspace collects nothing, yet its first message shows push works.
@pytest.mark.parametrize("collect", [True, False])
def test_a_pushed_message_makes_the_app_live(client, db_session, events_app, user, state, after, ts, collect):
    events_app.setup_state = state
    connect_workspace(db_session, events_app, user, collect=collect)

    with _capture_send_task():
        post_signed(client, f"/slack/events/{events_app.id}", message_event(GOOD_TEAM_ID, ts))

    db_session.refresh(events_app)
    assert events_app.setup_state == after


@pytest.mark.parametrize(
    "event, after",
    [
        ({"type": "message", "subtype": "message_changed", "channel": GOOD_CHANNEL_ID,
          "message": {"ts": "11.1", "user": "U1", "text": "edited"}}, "live"),
        ({"type": "message", "subtype": "message_deleted", "deleted_ts": "11.2",
          "channel": GOOD_CHANNEL_ID}, "live"),
        ({"type": "message", "subtype": "file_share", "ts": "11.3", "user": "U1", "text": "",
          "files": [{"id": "F1"}], "channel": GOOD_CHANNEL_ID}, "live"),
        ({"type": "reaction_added", "user": "U1", "reaction": "tada",
          "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": "11.4"}}, "live"),
        ({"type": "channel_rename", "channel": {"id": GOOD_CHANNEL_ID, "name": "renamed"}}, "live"),
        ({"type": "tokens_revoked", "tokens": {"oauth": ["U1"], "bot": ["B1"]}}, "signing_verified"),
        ({"type": "app_uninstalled"}, "signing_verified"),
        ({"type": "team_renamed", "channel": GOOD_CHANNEL_ID}, "signing_verified"),
        ({"type": "reaction_added", "user": "U1", "reaction": "tada",
          "item": {"type": "file", "file": "F1"}}, "signing_verified"),
        ({"type": "message", "ts": "11.5", "user": "U1", "text": "x", "channel": "not-a-channel"},
         "signing_verified"),
    ],
)
def test_only_events_that_queue_ingestion_make_the_app_live(client, db_session, events_app, user, event, after):
    events_app.setup_state = "signing_verified"
    connect_workspace(db_session, events_app, user)

    with _capture_send_task():
        post_signed(client, f"/slack/events/{events_app.id}", event_callback(event))

    db_session.refresh(events_app)
    assert events_app.setup_state == after


@pytest.mark.parametrize("envelope", [challenge("race"), message_event(GOOD_TEAM_ID, "10.1")])
def test_a_secret_pasted_while_an_old_signed_request_is_handled_stays_draft(
    client, db_session, events_app, user, envelope
):
    connect_workspace(db_session, events_app, user)

    def paste_new_secret(*_args):
        db_session.execute(
            update(SlackApp)
            .where(SlackApp.id == events_app.id)
            .values(signing_secret_encrypted=encrypt_value("newly-pasted"))
        )
        db_session.commit()
        return SIGNING_SECRET

    with _capture_send_task(), patch.object(slack_api, "decrypt_value", side_effect=paste_new_secret):
        post_signed(client, f"/slack/events/{events_app.id}", envelope)

    db_session.refresh(events_app)
    assert (events_app.signing_secret, events_app.setup_state) == ("newly-pasted", "draft")


@pytest.mark.parametrize(
    "event, expected, tasks",
    [
        ({"type": "message", "ts": "12.1", "user": "U1", "text": "x", "channel": GOOD_CHANNEL_ID},
         ("message", GOOD_CHANNEL_ID, "stored", None), 1),
        ({"type": "message", "subtype": "message_deleted", "deleted_ts": "12.2", "channel": GOOD_CHANNEL_ID},
         ("message.message_deleted", GOOD_CHANNEL_ID, "stored", None), 1),
        # Its message is stored too, so its text never sits in a payload.
        ({"type": "app_mention", "user": "U1", "text": "<@U2> hi", "channel": GOOD_CHANNEL_ID},
         ("app_mention", GOOD_CHANNEL_ID, "stored", None), 0),
        ({"type": "team_renamed", "channel": GOOD_CHANNEL_ID},
         ("team_renamed", GOOD_CHANNEL_ID, "no_item_type", event_callback({"type": "team_renamed", "channel": GOOD_CHANNEL_ID})
          | {"event_id": "Ev1"}), 0),
        ({"type": "message", "ts": "12.3", "user": "U1", "text": "x", "channel": "not-a-channel"},
         ("message", None, "rejected", None), 0),
        # Workspace events name no channel, and are left to raw subscriptions.
        ({"type": "team_join", "user": {"id": "U9"}},
         ("team_join", None, "no_item_type", event_callback({"type": "team_join", "user": {"id": "U9"}})
          | {"event_id": "Ev1"}), 0),
    ],
)
def test_each_event_is_a_raw_event_its_task_links(client, db_session, events_app, user, event, expected, tasks):
    connect_workspace(db_session, events_app, user)
    # The verification token is a credential, never logged.
    envelope = event_callback(event) | {"event_id": "Ev1", "token": "verification-token"}

    with _capture_send_task() as send:
        post_signed(client, f"/slack/events/{events_app.id}", envelope)

    (raw,) = raw_events(db_session)
    assert (raw.type, raw.container, raw.outcome, raw.payload) == expected
    assert (raw.source, raw.account_id, raw.owner_id, raw.delivery_id) == ("slack", events_app.id, user.id, "Ev1")
    assert [c.kwargs["kwargs"]["caused_by"] for c in send.call_args_list] == [raw.id] * tasks


@pytest.mark.parametrize(
    "workspace_collects, channel_collects, event",
    [
        # Stored channels decide for themselves; new ones follow their workspace.
        (True, False, {"type": "message", "ts": "14.1", "user": "U1", "text": "x", "channel": GOOD_CHANNEL_ID}),
        # Events with no item type are gated too.
        (True, False, {"type": "app_mention", "user": "U1", "text": "<@U2>", "channel": GOOD_CHANNEL_ID}),
        (False, None, {"type": "pin_added", "user": "U1", "channel_id": GOOD_CHANNEL_ID, "channel": GOOD_CHANNEL_ID}),
        (False, None, {"type": "pin_added", "user": "U1", "channel_id": GOOD_CHANNEL_ID}),
        (False, None, {"type": "message", "ts": "14.2", "user": "U1", "text": "x", "channel": GOOD_CHANNEL_ID}),
        (False, None, {"type": "message", "ts": "14.3", "user": "U1", "text": "x", "channel": "C99999999"}),
        (True, False, {"type": "reaction_added", "user": "U1", "reaction": "tada",
                       "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": "14.4"}}),
        (True, False, {"type": "channel_rename", "channel": {"id": GOOD_CHANNEL_ID, "name": "renamed"}}),
    ],
)
def test_events_in_channels_not_collected_leave_only_a_stub(
    client, db_session, events_app, user, workspace_collects, channel_collects, event
):
    connect_workspace(db_session, events_app, user, collect=workspace_collects)
    db_session.add(SlackChannel(id=GOOD_CHANNEL_ID, workspace_id=GOOD_TEAM_ID, name="c", channel_type="channel",
                                collect_messages=channel_collects))
    db_session.commit()

    with _capture_send_task() as send:
        response = post_signed(client, f"/slack/events/{events_app.id}", event_callback(event))

    assert response.status_code == 200
    send.assert_not_called()
    assert [(e.outcome, e.payload) for e in raw_events(db_session)] == [("not_collected", None)]


@pytest.mark.parametrize(
    "workspace_collects, channel_collects, outcome, container",
    [(True, False, "not_collected", None), (False, None, "not_collected", None), (True, True, "stored", GOOD_CHANNEL_ID)],
)
def test_a_delete_passes_whatever_the_channel_collects_but_an_untracked_ones_leaves_a_bare_stub(
    client, db_session, events_app, user, workspace_collects, channel_collects, outcome, container
):
    connect_workspace(db_session, events_app, user, collect=workspace_collects)
    db_session.add(SlackChannel(id=GOOD_CHANNEL_ID, workspace_id=GOOD_TEAM_ID, name="c", channel_type="channel",
                                collect_messages=channel_collects))
    db_session.commit()
    deleted = {"type": "message", "subtype": "message_deleted", "deleted_ts": "16.1", "channel": GOOD_CHANNEL_ID}

    with _capture_send_task() as send:
        post_signed(client, f"/slack/events/{events_app.id}", event_callback(deleted))

    assert [c.args[0] for c in send.call_args_list] == [slack_api.MARK_SLACK_MESSAGE_DELETED]
    assert [(e.outcome, e.container) for e in raw_events(db_session)] == [(outcome, container)]


def test_an_event_that_could_not_be_queued_is_taken_on_retry(client, db_session, events_app, user):
    connect_workspace(db_session, events_app, user)
    envelope = message_event(GOOD_TEAM_ID, "15.1") | {"event_id": "Ev3"}

    with _capture_send_task() as send:
        send.side_effect = [ConnectionError("broker down"), None]
        failed = post_signed(client, f"/slack/events/{events_app.id}", envelope)
        retried = post_signed(client, f"/slack/events/{events_app.id}", envelope)

    assert (failed.status_code, retried.status_code, send.call_count) == (500, 200, 2)
    assert [e.outcome for e in raw_events(db_session)] == ["stored"]


def test_a_retried_event_is_taken_once(client, db_session, events_app, user):
    connect_workspace(db_session, events_app, user)
    envelope = message_event(GOOD_TEAM_ID, "13.1") | {"event_id": "Ev2"}

    with _capture_send_task() as send:
        post_signed(client, f"/slack/events/{events_app.id}", envelope)
        post_signed(client, f"/slack/events/{events_app.id}", envelope | {"retry": 1})

    assert send.call_count == 1
    assert len(raw_events(db_session)) == 1


@pytest.mark.parametrize(
    "envelope, expected",
    [
        (challenge("logged"), ("url_verification", "no_item_type")),
        (message_event(GOOD_TEAM_ID, "13.2"), ("message", "rejected")),  # no credential in that team
        # Signed but shaped as Slack never sends.
        ({"type": "event_callback", "team_id": GOOD_TEAM_ID, "event": "message"}, ("event_callback", "rejected")),
        (event_callback({"type": "message", "ts": "13.3", "channel": [GOOD_CHANNEL_ID]}), ("event_callback", "rejected")),
        (event_callback({"type": "message", "subtype": "message_changed", "channel": GOOD_CHANNEL_ID, "message": "x"}),
         ("event_callback", "rejected")),
        (event_callback({"type": "message", "ts": "13.4", "channel": GOOD_CHANNEL_ID, "edited": "13.5"}),
         ("event_callback", "rejected")),
        (event_callback({"type": "message", "ts": "13.6", "channel": GOOD_CHANNEL_ID}) | {"event_id": ["Ev"]},
         ("event_callback", "rejected")),
    ],
)
def test_signed_requests_that_ingest_nothing_are_logged(client, db_session, events_app, envelope, expected):
    with _capture_send_task():
        post_signed(client, f"/slack/events/{events_app.id}", envelope)

    assert [(e.type, e.outcome) for e in raw_events(db_session)] == [expected]


def test_a_badly_signed_request_is_not_logged(client, db_session, events_app):
    post_signed(client, f"/slack/events/{events_app.id}", challenge("forged"), secret="wrong")

    assert raw_events(db_session) == []


def test_an_oversized_body_is_rejected(client, events_app):
    with patch.object(slack_api, "SLACK_EVENT_MAX_BODY_BYTES", 10):
        response = post_signed(client, f"/slack/events/{events_app.id}", challenge("too-big-body"))

    assert response.status_code == 401


@pytest.mark.parametrize(
    "last_miss_hours_ago, still_missing",
    [
        (1, True),  # the channel that missed may still be broken while others deliver
        (25, False),  # no poll has found a newly missed channel since
    ],
)
def test_event_records_when_it_arrived(client, db_session, events_app, last_miss_hours_ago, still_missing):
    missing_since = datetime.now(timezone.utc) - timedelta(hours=30)
    events_app.push_missing_since = missing_since
    events_app.push_last_miss_at = datetime.now(timezone.utc) - timedelta(hours=last_miss_hours_ago)
    db_session.commit()

    with _capture_send_task():
        post_signed(
            client,
            f"/slack/events/{events_app.id}",
            {
                "type": "event_callback",
                "team_id": GOOD_TEAM_ID,
                "event": {"type": "message", "ts": f"9.{last_miss_hours_ago}", "user": "U1", "text": "x",
                          "channel": GOOD_CHANNEL_ID},
            },
        )

    db_session.refresh(events_app)
    assert events_app.last_event_at is not None
    assert (events_app.push_missing_since == missing_since) is still_missing



@pytest.mark.parametrize(
    "event, links_items",
    [
        ({"type": "message", "ts": "17.1", "user": "U1", "text": "x", "channel": GOOD_CHANNEL_ID}, True),
        ({"type": "message", "subtype": "message_deleted", "deleted_ts": "17.2", "channel": GOOD_CHANNEL_ID}, True),
        ({"type": "app_mention", "user": "U1", "text": "<@U2> hi", "channel": GOOD_CHANNEL_ID}, False),
        # Their tasks update what's stored without writing item events.
        ({"type": "reaction_added", "user": "U1", "reaction": "tada",
          "item": {"type": "message", "channel": GOOD_CHANNEL_ID, "ts": "17.3"}}, False),
        ({"type": "channel_rename", "channel": {"id": GOOD_CHANNEL_ID, "name": "renamed"}}, False),
    ],
)
def test_a_raw_event_says_whether_an_ingest_task_will_link_its_items(client, db_session, events_app, user, event, links_items):
    connect_workspace(db_session, events_app, user)

    with _capture_send_task():
        post_signed(client, f"/slack/events/{events_app.id}", event_callback(event))

    (raw,) = raw_events(db_session)
    assert bool((raw.data or {}).get("links_items")) is links_items
