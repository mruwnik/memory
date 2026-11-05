"""A fake Beeper Server (and its Matrix homeserver) for tests, served through
``memory.common.beeper.inner_transport``.

Response shapes follow Beeper Client API 5.0.0 as Beeper Server 4.3.284 serves them.
"""

import json
from typing import Any

import httpx
import pytest

from memory.common import beeper, settings

URL = "http://beeper:23373"
HOMESERVER = "https://matrix.beeper.com/"
VERSION = "4.3.284"


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
        return self.error(404, "not_found")

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
