"""Beeper accounts: sign-in through a Beeper Server, and collection settings.

An owner adds an account on an allowlisted Server URL, then signs in step by
step: email, emailed code, then device verification (emoji or recovery key).
Memory never registers a Beeper account and never configures networks. Only
the owner can change an account, its networks or chats; tokens, codes and
recovery keys are never returned or logged.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.api.auth import assert_project_membership, get_interactive_user, resolve_user_filter
from memory.api.llm_keys import LLMKeyInput, LLMKeyView, apply_llm_key, llm_key_view
from memory.beeper import ingest, state
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.celery_app import SYNC_BEEPER_ACCOUNT
from memory.common.celery_app import app as celery_app
from memory.common.beeper import (
    BeeperClient,
    BeeperError,
    RegistrationRequired,
    SetupSession,
    Verification,
    VerificationAction,
    matrix_logout,
    origin,
    self_user_id,
    server_url_allowed,
)
from memory.common.db.connection import get_session
from memory.common.db.models import BeeperAccount, BeeperChat, BeeperNetwork, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/beeper", tags=["beeper"])

Sensitivity = Literal["public", "basic", "internal", "confidential"]
SetupStep = Literal["email", "code", "verify", "done"]
ACTIONS: dict[str, VerificationAction] = {
    "accept": "accept",
    "cancel": "cancel",
    "sas-start": "sas/start",
    "sas-confirm": "sas/confirm",
}
SERVER_TAKEN = "This Beeper Server already has an account in memory"
SIGNED_OUT_NOTE = (
    "Signed out of Beeper. The Beeper Server still holds this account's data: "
    "ask the operator to wipe its data directory before signing in on it again."
)


# --- Payloads ---


class ServerResponse(BaseModel):
    url: str
    in_use: bool


class AccountCreate(BaseModel):
    server_url: str
    name: str | None = None
    collect_by_default: bool = True


class AccountUpdate(BaseModel):
    name: str | None = None
    collect_by_default: bool | None = None
    is_active: bool | None = None


class EmailStep(BaseModel):
    email: EmailStr


class CodeStep(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class RecoveryKeyStep(BaseModel):
    recovery_key: str = Field(min_length=1, max_length=256)


class AccountResponse(BaseModel):
    id: int
    name: str | None
    server_url: str
    email: str | None
    beeper_user_id: str | None
    signed_in: bool
    verified: bool
    setup_state: str
    # What the setup form shows next
    step: SetupStep
    collect_by_default: bool
    is_active: bool
    server_version: str | None
    # False when the Server runs another build than the pinned one
    version_matches: bool
    # Whether the collector's WebSocket to the Server is up
    connected: bool
    last_reconcile_at: str | None
    last_event_at: str | None
    created_at: str | None


class VerificationResponse(BaseModel):
    id: str
    state: str
    direction: str
    methods: list[str]
    available_actions: list[str]
    other_device_name: str | None
    # Space-separated emoji to compare with the other device
    sas_emojis: str | None
    sas_decimals: str | None
    error_code: str | None


class SetupResponse(BaseModel):
    account: AccountResponse
    verification: VerificationResponse | None = None


class SignOutResponse(BaseModel):
    account: AccountResponse
    note: str


class NetworkResponse(BaseModel):
    id: int
    account_id: int
    network_account_id: str
    network: str
    bridge_type: str | None
    bridge_provider: str | None
    display_name: str | None
    status: str | None
    collect_messages: bool
    project_id: int | None
    sensitivity: str
    llm_key: LLMKeyView


class NetworkUpdate(BaseModel):
    collect_messages: bool | None = None
    project_id: int | None = None
    sensitivity: Sensitivity | None = None
    llm_key: LLMKeyInput | None = None


class ChatResponse(BaseModel):
    id: int
    network_id: int
    chat_id: str
    title: str | None
    chat_type: str | None
    is_merged: bool
    merged_into: str | None
    message_expiry_seconds: int | None
    collect_messages: bool | None
    effective_collect: bool
    project_id: int | None
    # null = inherit from the network
    sensitivity: str | None
    last_activity_at: str | None
    backfill_done: bool
    # Server kept answering with no older history, so the backfill gave up
    backfill_stopped: bool


class ChatUpdate(BaseModel):
    collect_messages: bool | None = None
    project_id: int | None = None
    sensitivity: Sensitivity | None = None


# --- Views ---


def iso(value) -> str | None:
    return value.isoformat() if value else None


def setup_step(account: BeeperAccount) -> SetupStep:
    if not account.signed_in:
        return "code" if account.setup_request_id else "email"
    return "done" if account.verified else "verify"


def account_view(account: BeeperAccount) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        name=account.name,
        server_url=account.server_url,
        email=account.email,
        beeper_user_id=account.beeper_user_id,
        signed_in=account.signed_in,
        verified=account.verified,
        setup_state=account.setup_state,
        step=setup_step(account),
        collect_by_default=account.collect_by_default,
        is_active=account.is_active,
        server_version=account.server_version,
        version_matches=account.server_version in (None, settings.BEEPER_SERVER_VERSION),
        connected=state.is_connected(account.id),
        last_reconcile_at=iso(account.last_reconcile_at),
        last_event_at=iso(account.last_event_at),
        created_at=iso(account.created_at),
    )


def verification_view(verification: Verification | None) -> VerificationResponse | None:
    if verification is None:
        return None
    return VerificationResponse(
        id=verification.id,
        state=verification.state,
        direction=verification.direction,
        methods=verification.methods,
        available_actions=verification.available_actions,
        other_device_name=verification.other_device.name if verification.other_device else None,
        sas_emojis=verification.sas.emojis if verification.sas else None,
        sas_decimals=verification.sas.decimals if verification.sas else None,
        error_code=verification.error.code if verification.error else None,
    )


def network_view(db: Session, user: User, network: BeeperNetwork) -> NetworkResponse:
    return NetworkResponse(
        id=network.id,
        account_id=network.account_id,
        network_account_id=network.network_account_id,
        network=network.network,
        bridge_type=network.bridge_type,
        bridge_provider=network.bridge_provider,
        display_name=network.display_name,
        status=network.status,
        collect_messages=network.collect_messages,
        project_id=network.project_id,
        sensitivity=network.sensitivity or "basic",
        llm_key=llm_key_view(db, user, network),
    )


def chat_view(chat: BeeperChat) -> ChatResponse:
    return ChatResponse(
        id=chat.id,
        network_id=chat.network_id,
        chat_id=chat.chat_id,
        title=chat.title,
        chat_type=chat.chat_type,
        is_merged=chat.is_merged,
        merged_into=chat.merged_into,
        message_expiry_seconds=chat.message_expiry_seconds,
        collect_messages=chat.collect_messages,
        effective_collect=chat.should_collect,
        project_id=chat.project_id,
        sensitivity=chat.sensitivity,
        last_activity_at=iso(chat.last_activity_at),
        backfill_done=chat.backfill_done,
        backfill_stopped=chat.backfill_stopped,
    )


# --- Ownership ---


def readable_account(db: Session, account_id: int, user: User) -> BeeperAccount:
    account = db.get(BeeperAccount, account_id)
    if account is None or not (account.created_by_user_id == user.id or has_admin_scope(user)):
        raise HTTPException(status_code=404, detail="Beeper account not found")
    return account


def owned_account(db: Session, account_id: int, user: User) -> BeeperAccount:
    account = db.get(BeeperAccount, account_id)
    if account is None or account.created_by_user_id != user.id:
        raise HTTPException(status_code=404, detail="Beeper account not found")
    return account


def owned_network(db: Session, network_id: int, user: User) -> BeeperNetwork:
    network = db.get(BeeperNetwork, network_id)
    if network is None or network.account.created_by_user_id != user.id:
        raise HTTPException(status_code=404, detail="Beeper network not found")
    return network


def owned_chat(db: Session, chat_id: int, user: User) -> BeeperChat:
    chat = db.get(BeeperChat, chat_id)
    if chat is None or chat.network.account.created_by_user_id != user.id:
        raise HTTPException(status_code=404, detail="Beeper chat not found")
    return chat


# --- Talking to the Server ---


def require_server_url(url: str) -> str:
    parsed = origin(url)
    if not parsed.startswith(("http://", "https://")) or not server_url_allowed(url):
        raise HTTPException(
            status_code=400,
            detail="That Beeper Server isn't allowed. Ask an admin to add it to BEEPER_SERVER_URLS.",
        )
    return parsed


def http_error(account: BeeperAccount, error: BeeperError) -> HTTPException:
    logger.warning("Beeper account %s: Server call failed (%s, %s)", account.id, error.status, error.code)
    if error.status == 0:
        return HTTPException(status_code=502, detail="The Beeper Server can't be reached")
    if error.code == "already_logged_in":
        return HTTPException(
            status_code=409,
            detail="This Beeper Server is already signed in. Ask the operator to wipe its data directory.",
        )
    if error.status == 401:
        return HTTPException(status_code=409, detail="The Beeper Server no longer accepts this sign-in")
    if error.status < 500:
        return HTTPException(status_code=400, detail=f"Beeper Server refused the request ({error.code})")
    return HTTPException(status_code=502, detail="The Beeper Server failed")


@asynccontextmanager
async def server(account: BeeperAccount, *, authed: bool = True) -> AsyncIterator[BeeperClient]:
    """A client for the account's Server; errors become HTTP errors, and the
    Server's version is recorded."""
    if not server_url_allowed(account.server_url):
        raise HTTPException(status_code=409, detail="This Beeper Server is no longer allowed")
    if authed and not account.signed_in:
        raise HTTPException(status_code=409, detail="Sign in to this Beeper account first")
    async with BeeperClient(account.server_url, account.token if authed else None) as client:
        try:
            yield client
        except BeeperError as e:
            raise http_error(account, e) from None
        finally:
            ingest.record_version(account, client.server_version)


def require_same_user(account: BeeperAccount, user_id: str | None) -> None:
    """A Server answering as another Beeper user than the one signed in is refused."""
    if account.beeper_user_id is None or user_id is None or user_id == account.beeper_user_id:
        return
    logger.warning("Beeper account %s: Server serves a different Beeper user", account.id)
    raise HTTPException(status_code=409, detail="This Beeper Server is signed in as a different Beeper user")


def apply_session(account: BeeperAccount, session: SetupSession) -> None:
    require_same_user(account, session.matrix.user_id if session.matrix else None)
    account.setup_state = session.state
    account.verified = session.e2ee.verified


def setup_result(db: Session, account: BeeperAccount, verification: Verification | None = None) -> SetupResponse:
    db.commit()
    db.refresh(account)
    return SetupResponse(account=account_view(account), verification=verification_view(verification))


# --- Accounts ---


@router.get("/servers")
def list_servers(
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[ServerResponse]:
    """The Beeper Servers an account can be added on."""
    in_use = set(db.scalars(select(BeeperAccount.server_url)))
    urls = sorted({origin(url) for url in settings.BEEPER_SERVER_URLS})
    return [ServerResponse(url=url, in_use=url in in_use) for url in urls]


@router.get("/accounts")
def list_accounts(
    user_id: int | None = None,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[AccountResponse]:
    """The caller's Beeper accounts; admins may list anyone's."""
    owner_id = resolve_user_filter(user_id, user, db)
    query = select(BeeperAccount).order_by(BeeperAccount.id)
    if owner_id is not None:
        query = query.where(BeeperAccount.created_by_user_id == owner_id)
    return [account_view(account) for account in db.scalars(query)]


@router.post("/accounts", status_code=201)
async def create_account(
    data: AccountCreate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> AccountResponse:
    """Add a Beeper account on a fresh, allowlisted Server. One Server holds one account."""
    server_url = require_server_url(data.server_url)
    if db.scalar(select(BeeperAccount.id).where(BeeperAccount.server_url == server_url)):
        raise HTTPException(status_code=409, detail=SERVER_TAKEN)
    account = BeeperAccount(
        created_by_user_id=user.id,
        server_url=server_url,
        name=data.name,
        collect_by_default=data.collect_by_default,
    )
    async with server(account, authed=False) as client:
        try:
            state = await client.setup_state()
        except BeeperError as e:
            if e.status == 401:
                raise HTTPException(
                    status_code=409,
                    detail="This Beeper Server is already signed in. Ask the operator to wipe its data directory.",
                ) from None
            raise
    if state.state != "needs-login":
        raise HTTPException(status_code=409, detail="This Beeper Server is already signed in")
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        # Another add on this Server won the race to the unique index.
        db.rollback()
        raise HTTPException(status_code=409, detail=SERVER_TAKEN) from None
    db.refresh(account)
    return account_view(account)


@router.get("/accounts/{account_id}")
def get_account(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> AccountResponse:
    return account_view(readable_account(db, account_id, user))


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: int,
    updates: AccountUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> AccountResponse:
    account = owned_account(db, account_id, user)
    if updates.name is not None:
        account.name = updates.name
    if updates.collect_by_default is not None:
        account.collect_by_default = updates.collect_by_default
    if updates.is_active is not None:
        account.is_active = updates.is_active
    db.commit()
    db.refresh(account)
    return account_view(account)


async def sign_out(account: BeeperAccount) -> None:
    token = account.token
    if token is None:
        return
    if account.homeserver is None:
        raise HTTPException(status_code=409, detail="This account's homeserver is unknown")
    try:
        await matrix_logout(account.homeserver, token)
    except BeeperError as e:
        raise http_error(account, e) from None
    account.token = None
    account.verified = False
    account.setup_state = "needs-login"
    logger.info("Beeper account %s signed out", account.id)


@router.post("/accounts/{account_id}/sign-out")
async def sign_out_account(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SignOutResponse:
    """End the account's Beeper session; its token stops working."""
    account = owned_account(db, account_id, user)
    await sign_out(account)
    account.setup_request_id = None
    db.commit()
    db.refresh(account)
    return SignOutResponse(account=account_view(account), note=SIGNED_OUT_NOTE)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> dict:
    """Sign the account out, then delete it with its networks, chats and messages."""
    account = owned_account(db, account_id, user)
    signed_out = True
    try:
        await sign_out(account)
    except HTTPException:
        signed_out = False
    db.delete(account)
    db.commit()
    return {"status": "deleted", "signed_out": signed_out, "note": SIGNED_OUT_NOTE}


# --- Sign-in steps ---


@router.post("/accounts/{account_id}/email")
async def setup_email(
    account_id: int,
    data: EmailStep,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SetupResponse:
    """Have Beeper email a sign-in code."""
    account = owned_account(db, account_id, user)
    if account.signed_in:
        raise HTTPException(status_code=409, detail="This Beeper account is already signed in")
    async with server(account, authed=False) as client:
        start = await client.setup_start()
        await client.setup_email(start.setup_request_id, data.email)
    account.setup_request_id = start.setup_request_id
    account.email = data.email
    return setup_result(db, account)


@router.post("/accounts/{account_id}/code")
async def setup_code(
    account_id: int,
    data: CodeStep,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SetupResponse:
    """Sign in with the emailed code. An email with no Beeper account is refused:
    memory never registers one."""
    account = owned_account(db, account_id, user)
    if account.signed_in:
        raise HTTPException(status_code=409, detail="This Beeper account is already signed in")
    if account.setup_request_id is None:
        raise HTTPException(status_code=409, detail="Enter the account's email first")
    async with server(account, authed=False) as client:
        result = await client.setup_response(account.setup_request_id, data.code)
    if isinstance(result, RegistrationRequired):
        account.setup_request_id = None
        db.commit()
        raise HTTPException(
            status_code=400,
            detail="There's no Beeper account for this email. Create one in the Beeper app first.",
        )

    login = result.matrix
    require_same_user(account, login.user_id)
    async with BeeperClient(account.server_url, login.access_token) as client:
        try:
            served = self_user_id(await client.accounts())
        except BeeperError as e:
            raise http_error(account, e) from None
        finally:
            ingest.record_version(account, client.server_version)
    if served != login.user_id:
        logger.warning("Beeper account %s: sign-in and /v1/accounts disagree on the user", account.id)
        raise HTTPException(status_code=409, detail="The Beeper Server serves a different Beeper user")

    account.token = login.access_token
    account.setup_request_id = None
    account.beeper_user_id = login.user_id
    account.homeserver = login.homeserver
    account.device_id = login.device_id
    apply_session(account, result.session)
    logger.info("Beeper account %s signed in", account.id)
    return setup_result(db, account, result.session.verification)


@router.get("/accounts/{account_id}/setup")
async def get_setup(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SetupResponse:
    """The Server's current sign-in state and any verification in progress."""
    account = owned_account(db, account_id, user)
    if not account.signed_in:
        return setup_result(db, account)
    async with server(account) as client:
        session = await client.setup_state()
        verification = session.verification or next(
            (v for v in await client.verifications() if v.state not in ("done", "cancelled")), None
        )
    apply_session(account, session)
    return setup_result(db, account, verification)


@router.post("/accounts/{account_id}/verifications")
async def start_verification(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SetupResponse:
    """Ask the account's other Beeper devices to approve this one."""
    account = owned_account(db, account_id, user)
    async with server(account) as client:
        verification, session = await client.start_verification()
    apply_session(account, session)
    return setup_result(db, account, verification)


@router.post("/accounts/{account_id}/verifications/{verification_id}/{action}")
async def verification_action(
    account_id: int,
    verification_id: str,
    action: Literal["accept", "cancel", "sas-start", "sas-confirm"],
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SetupResponse:
    """Accept, cancel, start emoji comparison, or confirm the emoji match."""
    account = owned_account(db, account_id, user)
    async with server(account) as client:
        verification, session = await client.verification_action(verification_id, ACTIONS[action])
    apply_session(account, session)
    return setup_result(db, account, verification)


@router.post("/accounts/{account_id}/recovery-key")
async def recovery_key(
    account_id: int,
    data: RecoveryKeyStep,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SetupResponse:
    """Verify this device with the account's recovery key."""
    account = owned_account(db, account_id, user)
    async with server(account) as client:
        session = await client.recovery_key(data.recovery_key)
    apply_session(account, session)
    return setup_result(db, account, session.verification)


# --- Networks and chats ---


@router.post("/accounts/{account_id}/refresh")
async def refresh_networks(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[NetworkResponse]:
    """Record the networks connected in Beeper; new ones follow the account's collect default."""
    account = owned_account(db, account_id, user)
    async with server(account) as client:
        accounts = await client.accounts()
    require_same_user(account, self_user_id(accounts))

    networks = ingest.refresh_networks(db, account, accounts)
    db.commit()
    return [network_view(db, user, network) for network in networks]


@router.get("/accounts/{account_id}/networks")
def list_networks(
    account_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[NetworkResponse]:
    account = readable_account(db, account_id, user)
    return [network_view(db, user, network) for network in sorted(account.networks, key=lambda n: n.id)]


@router.patch("/networks/{network_id}")
def update_network(
    network_id: int,
    updates: NetworkUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> NetworkResponse:
    network = owned_network(db, network_id, user)
    starts_collecting = updates.collect_messages is True and not network.collect_messages
    if updates.collect_messages is not None:
        network.collect_messages = updates.collect_messages
    if "project_id" in updates.model_fields_set:
        if updates.project_id is not None:
            assert_project_membership(db, user, updates.project_id)
        network.project_id = updates.project_id
    if updates.sensitivity is not None:
        network.sensitivity = updates.sensitivity
    apply_llm_key(db, user, network, updates.llm_key)
    if starts_collecting:
        ingest.resume_backfills(network.chats)
    db.commit()
    db.refresh(network)
    if starts_collecting:
        celery_app.send_task(SYNC_BEEPER_ACCOUNT, args=[network.account_id])
    return network_view(db, user, network)


@router.get("/networks/{network_id}/chats")
def list_chats(
    network_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[ChatResponse]:
    network = db.get(BeeperNetwork, network_id)
    if network is None:
        raise HTTPException(status_code=404, detail="Beeper network not found")
    readable_account(db, network.account_id, user)
    chats = db.scalars(
        select(BeeperChat)
        .where(BeeperChat.network_id == network.id)
        .order_by(BeeperChat.last_activity_at.desc().nulls_last(), BeeperChat.id)
    )
    return [chat_view(chat) for chat in chats]


@router.patch("/chats/{chat_id}")
def update_chat(
    chat_id: int,
    updates: ChatUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> ChatResponse:
    """collect_messages and sensitivity: a value overrides the network; null inherits it."""
    chat = owned_chat(db, chat_id, user)
    collected = chat.should_collect
    if "collect_messages" in updates.model_fields_set:
        chat.collect_messages = updates.collect_messages
    if "project_id" in updates.model_fields_set:
        if updates.project_id is not None:
            assert_project_membership(db, user, updates.project_id)
        chat.project_id = updates.project_id
    if "sensitivity" in updates.model_fields_set:
        chat.sensitivity = updates.sensitivity
    starts_collecting = chat.should_collect and not collected
    if starts_collecting:
        ingest.resume_backfills([chat])
    db.commit()
    db.refresh(chat)
    if starts_collecting:
        state.queue_chat_sync(chat.id)
    return chat_view(chat)
