"""A fake Beeper Server (and its Matrix homeserver) for tests, served through
``memory.common.beeper.inner_transport``.

Response shapes follow Beeper Client API 5.0.0 as Beeper Server 4.3.284 serves them.
"""

import json
from typing import Any

import httpx
import pytest

from memory.common import beeper, settings
from memory.common.db.models import BeeperAccount

URL = "http://beeper:23373"
HOMESERVER = "https://matrix.beeper.com/"
VERSION = "4.3.284"


def event(kind: str, chat_id: str, ids: list[str], entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": kind, "seq": 1, "ts": "2026-11-05T12:00:00.000Z", "chatID": chat_id, "ids": ids}
    if entries is not None:
        body["entries"] = entries
    return body


class FakeBeeperServer:
    def __init__(self):
        self.token = "fake-access-token"
        self.code = "424242"
        self.recovery_key = "EsTc fake recovery key"
        self.user_id = "@me:beeper.com"
        self.version = VERSION
        self.signed_in = False
        self.verified = False
        self.unregistered_emails: set[str] = set()
        self.emails_sent: list[str] = []
        self.verification: dict[str, Any] | None = None
        self.accounts: list[dict[str, Any]] = [
            {
                "accountID": "slackgo.T1-U1",
                "loginID": "T1-U1",
                "bridge": {"id": "slackgo", "type": "slackgo", "provider": "cloud"},
                "network": "Slack",
                "user": {"id": "T1-U1", "fullName": "Me", "displayText": "Team - me@example.com", "isSelf": True},
                "status": "connected",
            }
        ]
        self.paths: list[str] = []
        self.chats: list[dict[str, Any]] = []
        # Per chat, oldest first
        self.messages: dict[str, list[dict[str, Any]]] = {}
        # Per chat, how many direction=before pages come back empty with hasMore,
        # as Server does while it asks the bridge for older history
        self.empty_pages: dict[str, int] = {}
        # Chats whose direction=after pages are empty with hasMore, as Server 4.3.284
        # answers for some Matrix rooms
        self.stuck_after: set[str] = set()
        # Chats whose direction=before pages always repeat the cursor they were asked for
        self.frozen_cursor: set[str] = set()
        # Matrix event id -> message id; GET .../messages/{id} takes either, as Server does
        self.event_ids: dict[str, str] = {}
        self.assets: dict[str, bytes] = {}
        self.page_size = 2
        self.next_sort_key = 100

    # --- content ---

    def add_chat(self, chat_id: str, account_id: str = "slackgo.T1-U1", **fields: Any) -> dict[str, Any]:
        chat = {
            "id": chat_id, "localChatID": str(len(self.chats) + 1), "accountID": account_id,
            "network": "Slack", "title": f"Chat {chat_id}", "type": "group",
            "participants": {"items": [], "hasMore": False, "total": 0},
            "lastActivity": "2026-11-05T10:00:00.000Z", "messageExpirySeconds": None, **fields,
        }
        self.chats.append(chat)
        self.messages.setdefault(chat_id, [])
        return chat

    def add_message(self, chat_id: str, message_id: str, text: str | None = None, **fields: Any) -> dict[str, Any]:
        self.next_sort_key += 1
        chat = next(c for c in self.chats if c["id"] == chat_id)
        message = {
            "id": message_id, "chatID": chat_id, "accountID": chat["accountID"],
            "senderID": "@alice:beeper.local", "senderName": "Alice",
            "timestamp": f"2026-11-0{1 + self.next_sort_key % 4}T10:00:{self.next_sort_key % 60:02d}.000Z",
            "sortKey": str(self.next_sort_key), "type": "TEXT", "isSender": False, "isDeleted": False,
            "mentions": [], **fields,
        }
        if text is not None:
            message["text"] = text
        self.messages[chat_id].append(message)
        return message

    def message(self, chat_id: str, message_id: str) -> dict[str, Any]:
        return next(m for m in self.messages[chat_id] if m["id"] == message_id)

    # --- live events, in the order Server 4.3.284 sends them ---

    def send(self, chat_id: str, message_id: str, text: str, event_id: str) -> list[dict[str, Any]]:
        """Listed by its id, then again by its Matrix event id once the homeserver echoes it."""
        message = self.add_message(chat_id, message_id, text)
        self.event_ids[event_id] = message_id
        return [
            event("message.upserted", chat_id, [message_id], [message]),
            event("message.upserted", chat_id, [event_id], [message]),
        ]

    def event_id(self, message_id: str) -> str:
        return next(e for e, m in self.event_ids.items() if m == message_id)

    def react(self, chat_id: str, reaction_id: str, parent_id: str, participant: str, key: str) -> list[dict[str, Any]]:
        """The hidden REACTION row by its id, then the parent by its event id with its reactions."""
        reaction = self.add_message(chat_id, reaction_id, type="REACTION", isHidden=True, linkedMessageID=parent_id)
        parent = self.message(chat_id, parent_id)
        parent["reactions"] = [
            *parent.get("reactions", []), {"id": participant, "reactionKey": key, "participantID": participant}
        ]
        return [
            event("message.upserted", chat_id, [reaction_id], [reaction]),
            event("message.upserted", chat_id, [self.event_id(parent_id)], [parent]),
        ]

    def unreact(self, chat_id: str, reaction_id: str) -> list[dict[str, Any]]:
        """The REACTION row deleted by its id, then ``message.deleted`` naming the
        parent's event id: the parent still exists, without that reaction."""
        reaction = self.message(chat_id, reaction_id)
        self.messages[chat_id].remove(reaction)
        parent = self.message(chat_id, reaction["linkedMessageID"])
        parent["reactions"] = []
        return [
            event("message.upserted", chat_id, [reaction_id], [{**reaction, "isDeleted": True}]),
            event("message.deleted", chat_id, [self.event_id(parent["id"])]),
        ]

    def delete(self, chat_id: str, message_id: str) -> list[dict[str, Any]]:
        message = self.message(chat_id, message_id)
        message["isDeleted"] = True
        return [event("message.upserted", chat_id, [message_id], [message])]

    # --- shapes ---

    def session(self) -> dict[str, Any]:
        state = "needs-login"
        if self.signed_in:
            state = "ready" if self.verified else "needs-verification"
        body: dict[str, Any] = {
            "state": state,
            "e2ee": {
                "initialized": self.signed_in, "secretStorage": True, "crossSigning": True,
                "verified": self.verified,
                "secrets": {k: self.verified for k in ("masterKey", "selfSigningKey", "userSigningKey", "megolmBackupKey", "recoveryKey")},
                "keyBackup": self.verified, "firstSyncDone": self.verified, "hasBackedUpRecoveryKey": False,
            },
        }
        if self.signed_in:
            body["matrix"] = {"userID": self.user_id, "deviceID": "DEVICE", "homeserver": HOMESERVER}
        if self.verification:
            body["verification"] = self.verification
        return body

    def all_accounts(self) -> list[dict[str, Any]]:
        own = {
            "accountID": "matrix",
            "loginID": self.user_id,
            "bridge": {"id": "matrix", "type": "matrix", "provider": "cloud"},
            "network": "Beeper",
            "user": {"id": self.user_id, "username": self.user_id.lstrip("@"), "isSelf": True},
            "status": "connected",
        }
        return [own, *self.accounts]

    def set_verification(self, state: str, actions: list[str], sas: bool = False) -> dict[str, Any]:
        self.verification = {
            "id": "verification-1", "purpose": "login", "direction": "outgoing", "state": state,
            "methods": ["sas"], "availableActions": actions,
            "otherDevice": {"id": "PHONE", "name": "Phone"},
        }
        if sas:
            self.verification["sas"] = {"emojis": "🐶 🐱 🦁 🐎 🦄 🐷 🐘", "decimals": "1234 5678 9012"}
        return self.verification

    # --- transport ---

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(f"{request.method} {request.url.path}")
        if request.url.host == "matrix.beeper.com":
            return self.matrix(request)
        authed = request.headers.get("authorization") == f"Bearer {self.token}" and self.signed_in
        body = json.loads(request.content) if request.content else {}
        route = (request.method, request.url.path)
        public = {("GET", "/v1/info"), ("POST", "/v1/app/setup/start"), ("POST", "/v1/app/setup/email"),
                  ("POST", "/v1/app/setup/response"), ("POST", "/v1/app/setup/register")}
        if route == ("GET", "/v1/app/setup") and not self.signed_in:
            return self.reply(self.session())
        if route not in public and not authed:
            return self.error(401, "unauthorized")

        match route:
            case ("POST", "/v1/app/setup/start"):
                if self.signed_in:
                    return self.error(409, "already_logged_in")
                return self.reply({"setupRequestID": "setup-1", "signInMethods": ["email"]})
            case ("POST", "/v1/app/setup/email"):
                self.emails_sent.append(body["email"])
                return httpx.Response(204, headers=self.headers())
            case ("POST", "/v1/app/setup/response"):
                if body.get("setupRequestID") != "setup-1" or body.get("response") != self.code:
                    return self.error(400, "invalid_code")
                if self.emails_sent[-1] in self.unregistered_emails:
                    return self.reply({
                        "registrationRequired": True, "setupRequestID": "setup-1", "leadToken": "lead-secret",
                        "copy": {"title": "t", "usernamePlaceholder": "Username", "submit": "Continue", "terms": "x"},
                    })
                self.signed_in = True
                return self.reply({
                    "matrix": {"userID": self.user_id, "deviceID": "DEVICE", "accessToken": self.token, "homeserver": HOMESERVER},
                    "session": self.session(),
                })
            case ("POST", "/v1/app/setup/register"):
                raise AssertionError("memory must never register a Beeper account")
            case ("GET", "/v1/app/setup"):
                return self.reply(self.session())
            case ("GET", "/v1/app/setup/verifications"):
                return self.reply({"items": [self.verification] if self.verification else []})
            case ("POST", "/v1/app/setup/verifications"):
                verification = self.set_verification("requested", ["cancel"])
                return self.reply({"verification": verification, "session": self.session()})
            case ("POST", "/v1/app/setup/verification/recovery-key"):
                if body.get("recoveryKey") != self.recovery_key:
                    return self.error(400, "invalid_recovery_key")
                self.verified = True
                return self.reply({"session": self.session()})
            case ("GET", "/v1/accounts"):
                return self.reply(self.all_accounts())
        if request.method == "POST" and request.url.path.startswith("/v1/app/setup/verifications/verification-1/"):
            return self.verification_action(request.url.path.rsplit("verification-1/", 1)[1])
        if request.method == "GET":
            return self.read(request)
        return self.error(404, "not_found")

    def read(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        parts = request.url.path.split("/")[1:]
        match parts:
            case ["v1", "chats"]:
                return self.reply(self.chat_page(params.get("cursor"), params.get("direction")))
            case ["v1", "chats", chat_id]:
                chat = next((c for c in self.chats if c["id"] == chat_id), None)
                return self.reply(chat) if chat else self.error(404, "not_found")
            case ["v1", "chats", chat_id, "messages"] if chat_id in self.messages:
                return self.reply(self.message_page(chat_id, params.get("cursor"), params.get("direction")))
            case ["v1", "chats", chat_id, "messages", message_id] if chat_id in self.messages:
                message_id = self.event_ids.get(message_id, message_id)
                found = [m for m in self.messages[chat_id] if m["id"] == message_id]
                return self.reply(found[0]) if found else self.error(404, "not_found")
            case ["v1", "assets", "serve"] if params.get("url") in self.assets:
                # Like Server: no useful content type, so the mimeType must come from the message.
                return httpx.Response(
                    200, content=self.assets[params["url"]],
                    headers={**self.headers(), "content-type": "application/octet-stream"},
                )
        return self.error(404, "not_found")

    def chat_page(self, cursor: str | None, direction: str | None) -> dict[str, Any]:
        """Chats newest activity first, paged by position."""
        start = int(cursor) if cursor and direction == "before" else 0
        items = self.chats[start : start + self.page_size]
        end = start + len(items)
        return {
            "items": items, "hasMore": end < len(self.chats),
            "oldestCursor": str(end) if items else None, "newestCursor": str(start) if items else None,
        }

    def message_page(self, chat_id: str, cursor: str | None, direction: str | None) -> dict[str, Any]:
        """A page of messages newest first; the cursors are sort keys."""
        messages = self.messages[chat_id]
        key = lambda m: int(m["sortKey"])  # noqa: E731
        if cursor and direction == "after" and chat_id in self.stuck_after:
            return {"items": [], "hasMore": True, "oldestCursor": None, "newestCursor": None}
        if cursor and direction == "after":
            newer = [m for m in messages if key(m) > int(cursor)]
            items, has_more = newer[: self.page_size], len(newer) > self.page_size
        else:
            older = [m for m in messages if not cursor or key(m) < int(cursor)]
            if cursor and chat_id in self.frozen_cursor:
                items = sorted(older[-self.page_size :], key=key, reverse=True)
                return {"items": items, "hasMore": True, "oldestCursor": cursor, "newestCursor": cursor}
            if cursor and self.empty_pages.get(chat_id):
                self.empty_pages[chat_id] -= 1
                return {"items": [], "hasMore": True, "oldestCursor": None, "newestCursor": None}
            items, has_more = older[-self.page_size :], len(older) > self.page_size
        items = sorted(items, key=key, reverse=True)
        return {
            "items": items, "hasMore": has_more,
            "oldestCursor": items[-1]["sortKey"] if items else None,
            "newestCursor": items[0]["sortKey"] if items else None,
        }

    def verification_action(self, action: str) -> httpx.Response:
        if action == "sas/start":
            verification = self.set_verification("sas_ready", ["sas.confirm", "cancel"], sas=True)
        elif action == "sas/confirm":
            self.verified = True
            verification = self.set_verification("done", [])
            self.verification = None
        elif action == "cancel":
            verification = self.set_verification("cancelled", [])
            self.verification = None
        else:
            return self.error(400, "invalid_action")
        return self.reply({"verification": verification, "session": self.session()})

    def matrix(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/_matrix/client/v3/logout":
            return httpx.Response(404)
        if request.headers.get("authorization") != f"Bearer {self.token}" or not self.signed_in:
            return httpx.Response(401, json={"errcode": "M_UNKNOWN_TOKEN"})
        self.signed_in = False
        self.verified = False
        return httpx.Response(200, json={})

    def headers(self) -> dict[str, str]:
        return {"X-Beeper-Desktop-Version": self.version}

    def reply(self, body: Any) -> httpx.Response:
        return httpx.Response(200, json=body, headers=self.headers())

    def error(self, status: int, code: str) -> httpx.Response:
        return httpx.Response(status, json={"message": "error", "code": code}, headers=self.headers())


@pytest.fixture
def fake_beeper(monkeypatch) -> FakeBeeperServer:
    server = FakeBeeperServer()
    monkeypatch.setattr(settings, "BEEPER_SERVER_URLS", [URL])
    monkeypatch.setattr(settings, "BEEPER_HOMESERVER_ORIGINS", ["https://matrix.beeper.com"])
    monkeypatch.setattr(beeper, "inner_transport", lambda: httpx.MockTransport(server.handle))
    return server


@pytest.fixture
def beeper_account(db_session, fake_beeper, regular_user) -> BeeperAccount:
    """A signed-in, verified account of regular_user on the fake Server."""
    fake_beeper.signed_in = fake_beeper.verified = True
    account = BeeperAccount(
        created_by_user_id=regular_user.id,
        server_url=URL,
        beeper_user_id=fake_beeper.user_id,
        homeserver=HOMESERVER,
        setup_state="ready",
        verified=True,
    )
    account.token = fake_beeper.token
    db_session.add(account)
    db_session.commit()
    return account
