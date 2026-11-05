"""A typed client for Beeper Server's Client API.

Beeper Servers usually sit on a private network, which ``public_http_client``
rightly refuses, so memory only talks to the origins an admin listed in
BEEPER_SERVER_URLS, and sends a token to a Matrix homeserver only when it is in
BEEPER_HOMESERVER_ORIGINS. Every request is checked again in the transport, so
a redirect or a stored URL from an older allowlist goes nowhere. Redirects are
never followed and proxy settings are ignored.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from memory.common import settings

logger = logging.getLogger(__name__)

VERSION_HEADER = "x-beeper-desktop-version"
VerificationAction = Literal["accept", "cancel", "sas/start", "sas/confirm"]


def origin(url: str) -> str:
    parsed = urlparse(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def server_url_allowed(url: str) -> bool:
    return origin(url) in {origin(o) for o in settings.BEEPER_SERVER_URLS}


def homeserver_allowed(url: str) -> bool:
    return origin(url) in {origin(o) for o in settings.BEEPER_HOMESERVER_ORIGINS}


class BeeperURLRefused(httpx.ConnectError):
    """A request to an origin that isn't on its allowlist."""


class AllowlistTransport(httpx.AsyncBaseTransport):
    def __init__(self, allowed: Callable[[str], bool], inner: httpx.AsyncBaseTransport):
        self.allowed = allowed
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.allowed(str(request.url)):
            raise BeeperURLRefused("origin is not on the Beeper allowlist", request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def inner_transport() -> httpx.AsyncBaseTransport:
    """The network transport under the allowlist check; tests swap in a fake server."""
    return httpx.AsyncHTTPTransport(trust_env=False)


def allowlisted_client(allowed: Callable[[str], bool], **kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=AllowlistTransport(allowed, inner_transport()),
        timeout=settings.BEEPER_REQUEST_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        **kwargs,
    )


class BeeperError(Exception):
    """A failed call: an error status with Beeper's error ``code``, or a
    transport failure (status 0). Carries no response text."""

    def __init__(self, status: int, code: str | None = None):
        self.status = status
        self.code = code
        super().__init__(f"Beeper Server error {status} ({code})")


# --- Response shapes ---


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class SAS(Model):
    emojis: str
    decimals: str | None = None


class OtherDevice(Model):
    id: str
    name: str | None = None


class VerificationError(Model):
    code: str
    reason: str


class Verification(Model):
    id: str
    purpose: str
    direction: str
    # requested, ready, sas_ready, qr_scanned, done, cancelled, error
    state: str
    methods: list[str] = []
    available_actions: list[str] = []
    other_device: OtherDevice | None = None
    sas: SAS | None = None
    error: VerificationError | None = None


class MatrixIdentity(Model):
    user_id: str = Field(alias="userID")
    device_id: str = Field(alias="deviceID")
    homeserver: str


class E2EE(Model):
    verified: bool
    first_sync_done: bool = False


class SetupSession(Model):
    # needs-login, initializing, needs-cross-signing-setup, needs-verification,
    # needs-secrets, needs-first-sync, ready
    state: str
    matrix: MatrixIdentity | None = None
    e2ee: E2EE
    verification: Verification | None = None


class SetupStart(Model):
    setup_request_id: str = Field(alias="setupRequestID")
    sign_in_methods: list[str] = []


class MatrixLogin(MatrixIdentity):
    access_token: str


class SignedIn(Model):
    matrix: MatrixLogin
    session: SetupSession


class RegistrationRequired(Model):
    """The email has no Beeper account. Its lead token is deliberately not kept."""

    registration_required: Literal[True]


class Bridge(Model):
    id: str
    type: str
    provider: str | None = None


class BeeperUser(Model):
    id: str
    username: str | None = None
    full_name: str | None = None
    display_text: str | None = None
    is_self: bool = False


class NetworkAccount(Model):
    account_id: str = Field(alias="accountID")
    network: str | None = None
    bridge: Bridge
    user: BeeperUser
    # connected, connecting, backfilling, connection_required, ...
    status: str


class ChatParticipants(Model):
    items: list[BeeperUser] = []
    has_more: bool = False
    total: int = 0


class Chat(Model):
    id: str
    local_chat_id: str | None = Field(None, alias="localChatID")
    account_id: str = Field(alias="accountID")
    network: str | None = None
    title: str | None = None
    type: str
    participants: ChatParticipants = ChatParticipants()
    last_activity: datetime | None = None
    message_expiry_seconds: int | None = None
    merge: dict[str, Any] | None = None
    merged_into_chat_id: str | None = Field(None, alias="mergedIntoChatID")


class Transcription(Model):
    transcription: str
    engine: str | None = None
    language: str | None = None


class Attachment(Model):
    id: str | None = None
    type: str
    src_url: str | None = Field(None, alias="srcURL")
    mime_type: str | None = None
    file_name: str | None = None
    file_size: float | None = None
    is_voice_note: bool = False
    duration: float | None = None
    transcription: Transcription | None = None


class Reaction(Model):
    id: str
    reaction_key: str
    participant_id: str = Field(alias="participantID")


class Message(Model):
    id: str
    chat_id: str = Field(alias="chatID")
    account_id: str = Field(alias="accountID")
    sender_id: str = Field(alias="senderID")
    sender_name: str | None = None
    timestamp: datetime
    sort_key: str
    type: str | None = None
    text: str | None = None
    edited_timestamp: datetime | None = None
    is_sender: bool = False
    is_hidden: bool = False
    is_deleted: bool = False
    attachments: list[Attachment] = []
    linked_message_id: str | None = Field(None, alias="linkedMessageID")
    mentions: list[str] | None = None
    reactions: list[Reaction] = []


class Page[T](Model):
    items: list[T]
    has_more: bool
    oldest_cursor: str | None = None
    newest_cursor: str | None = None


# --- Client ---


class BeeperClient:
    """One Beeper Server, called with an account's token (none before sign-in)."""

    def __init__(self, server_url: str, token: str | None = None):
        self.base_url = origin(server_url)
        self.token = token
        self.server_version: str | None = None
        self.http = allowlisted_client(server_url_allowed)

    async def __aenter__(self) -> "BeeperClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.http.aclose()

    @property
    def version_matches(self) -> bool:
        return self.server_version in (None, settings.BEEPER_SERVER_VERSION)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = await self.http.request(
                method, self.base_url + path, json=json, params=params, headers=headers
            )
        except httpx.HTTPError as e:
            raise BeeperError(0, type(e).__name__) from None
        self.server_version = response.headers.get(VERSION_HEADER, self.server_version)
        # A redirect could lead anywhere, so it fails like an error status.
        if not response.is_success:
            raise BeeperError(response.status_code, error_code(response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise BeeperError(response.status_code, "invalid_json") from None

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, json=body if body is not None else {})

    # Setup (sign-in and device verification)

    async def setup_state(self) -> SetupSession:
        return SetupSession.model_validate(await self.get("/v1/app/setup"))

    async def setup_start(self) -> SetupStart:
        return SetupStart.model_validate(await self.post("/v1/app/setup/start"))

    async def setup_email(self, setup_request_id: str, email: str) -> None:
        await self.post("/v1/app/setup/email", {"setupRequestID": setup_request_id, "email": email})

    async def setup_response(self, setup_request_id: str, code: str) -> SignedIn | RegistrationRequired:
        data = await self.post(
            "/v1/app/setup/response", {"setupRequestID": setup_request_id, "response": code}
        )
        if isinstance(data, dict) and data.get("registrationRequired"):
            return RegistrationRequired.model_validate(data)
        return SignedIn.model_validate(data)

    async def start_verification(self) -> tuple[Verification, SetupSession]:
        data = await self.post("/v1/app/setup/verifications", {"purpose": "login"})
        return Verification.model_validate(data["verification"]), SetupSession.model_validate(data["session"])

    async def verifications(self) -> list[Verification]:
        data = await self.get("/v1/app/setup/verifications")
        return [Verification.model_validate(v) for v in data["items"]]

    async def verification_action(
        self, verification_id: str, action: VerificationAction
    ) -> tuple[Verification, SetupSession]:
        data = await self.post(f"/v1/app/setup/verifications/{quote_id(verification_id)}/{action}")
        return Verification.model_validate(data["verification"]), SetupSession.model_validate(data["session"])

    async def recovery_key(self, recovery_key: str) -> SetupSession:
        data = await self.post("/v1/app/setup/verification/recovery-key", {"recoveryKey": recovery_key})
        return SetupSession.model_validate(data["session"])

    # Reading

    async def accounts(self) -> list[NetworkAccount]:
        return [NetworkAccount.model_validate(a) for a in await self.get("/v1/accounts")]

    async def chats(
        self,
        cursor: str | None = None,
        direction: Literal["before", "after"] | None = None,
        limit: int | None = None,
    ) -> Page[Chat]:
        data = await self.get("/v1/chats", cursor=cursor, direction=direction, limit=limit)
        return Page[Chat].model_validate(data)

    async def chat(self, chat_id: str, max_participant_count: int | None = None) -> Chat:
        data = await self.get(f"/v1/chats/{quote_id(chat_id)}", maxParticipantCount=max_participant_count)
        return Chat.model_validate(data)

    async def messages(
        self,
        chat_id: str,
        cursor: str | None = None,
        direction: Literal["before", "after"] | None = None,
    ) -> Page[Message]:
        data = await self.get(f"/v1/chats/{quote_id(chat_id)}/messages", cursor=cursor, direction=direction)
        return Page[Message].model_validate(data)


def quote_id(value: str) -> str:
    return quote(value, safe="")


def error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def self_user_id(accounts: list[NetworkAccount]) -> str | None:
    """The Beeper (Matrix) user the Server is signed in as."""
    return next(
        (a.user.id for a in accounts if a.bridge.type == "matrix" and a.user.is_self), None
    )


async def matrix_logout(homeserver: str, token: str) -> None:
    """Sign the token's Matrix session out: Beeper Server has no sign-out of its own."""
    if not homeserver_allowed(homeserver):
        raise BeeperError(0, "homeserver_not_allowed")
    async with allowlisted_client(homeserver_allowed) as http:
        try:
            response = await http.post(
                origin(homeserver) + "/_matrix/client/v3/logout",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as e:
            raise BeeperError(0, type(e).__name__) from None
    # 401: the token is already dead, which is what signing out wants.
    if not response.is_success and response.status_code != 401:
        raise BeeperError(response.status_code, error_code(response))
