"""One-time codes a user DMs to their own Discord bot to prove which Discord account is theirs.

Only the user's own bots count: a bot's owner reads its DMs, so a code sent to
someone else's bot could be replayed from the owner's account. A valid code
links nothing yet: the bot names the Memory login it belongs to and waits for
a ``confirm`` reply, so someone talked into sending another person's code sees
whose it is before their account goes to them. A code posted anywhere else, or
edited into a message, is spent unused. Neither message is ever stored.
Worker-safe.
"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordBot, DiscordChannel, DiscordUser, User
from memory.common.discord import send_dm
from memory.common.identity import link_discord_user
from memory.common.redis_pool import get_redis

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=15)
CONFIRM_TTL = timedelta(minutes=5)
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 3600
LINK_COMMAND = re.compile(r"\s*link\s+(\d{8})\s*", re.IGNORECASE)
CONFIRM_COMMAND = re.compile(r"\s*confirm\s*", re.IGNORECASE)
FAILED_REPLY = "That code is wrong, used or expired. Get a new one in Memory."
REDACTED = "[link code redacted]"


def pending_key(bot_id: int, author_id: int) -> str:
    return f"discord-link:pending:{bot_id}:{author_id}"


def has_pending(bot_id: Any, author_id: Any) -> bool:
    try:
        return bool(get_redis().exists(pending_key(int(bot_id), int(author_id))))
    except (TypeError, ValueError):
        return False


def is_link_text(bot_id: Any, author_id: Any, content: Any) -> bool:
    """A ``link <code>`` message, or a ``confirm`` the author owes the bot."""
    text = str(content or "")
    if LINK_COMMAND.fullmatch(text):
        return True
    return CONFIRM_COMMAND.fullmatch(text) is not None and has_pending(bot_id, author_id)


def author_of(data: dict[str, Any]) -> Any:
    author = data.get("author")
    return author.get("id") if isinstance(author, dict) else None


def redact_message(kwargs: dict[str, Any]) -> dict[str, Any]:
    """add_discord_message's kwargs without the body of a link message."""
    if not is_link_text(kwargs.get("bot_id"), kwargs.get("author_id"), kwargs.get("content")):
        return kwargs
    return {**kwargs, "content": REDACTED}


def redact_event(kwargs: dict[str, Any]) -> dict[str, Any]:
    """handle_discord_event's kwargs (or a pushed event, given its bot) without
    the body of a link message."""
    data = kwargs.get("data")
    if not isinstance(data, dict) or not is_link_text(kwargs.get("bot_id"), author_of(data), data.get("content")):
        return kwargs
    return {**kwargs, "data": {**data, "content": REDACTED}}


def redact_envelope(envelope: dict[str, Any], bot_id: int) -> dict[str, Any]:
    """A pushed Discord event without the body of a link message."""
    event = envelope.get("event")
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict) or not is_link_text(bot_id, author_of(data), data.get("content")):
        return envelope
    return {**envelope, "event": {**cast(dict, event), "data": {**data, "content": REDACTED}}}


def is_link_dm(bot_id: int, event_type: str, data: dict[str, Any]) -> bool:
    """Whether an event is a new message, in no server, that is a link message."""
    return (
        event_type == "MESSAGE_CREATE"
        and data.get("guild_id") is None
        and is_link_text(bot_id, author_of(data), data.get("content"))
    )


def spend_posted_code(content: Any) -> bool:
    """Spend the code of a ``link <code>`` seen anywhere but a new DM (a server
    channel, an edit, a DM Memory doesn't see through), with no reply. Whether
    it was one, so the caller drops the message."""
    match = LINK_COMMAND.fullmatch(str(content or ""))
    if match is None:
        return False
    take_code(match[1])
    return True


def now() -> datetime:
    return datetime.now(timezone.utc)


def digest(code: str) -> str:
    """A keyed digest, so the stored ones can't be brute-forced from Redis
    alone: there are only 10**8 codes."""
    secret = settings.TRANSFER_TOKEN_SECRET
    if not secret:
        raise RuntimeError("Discord link codes need TRANSFER_TOKEN_SECRET or SECRETS_ENCRYPTION_KEY")
    return hmac.new(secret.encode(), f"discord-link:{code}".encode(), hashlib.sha256).hexdigest()


def code_key(code_digest: str) -> str:
    return f"discord-link:code:{code_digest}"


def user_key(user_id: int) -> str:
    return f"discord-link:user:{user_id}"


def failures_key(author_id: int) -> str:
    return f"discord-link:failures:{author_id}"


def seen_key(message_id: int) -> str:
    return f"discord-link:message:{message_id}"


def issue_code(user_id: int) -> tuple[str, datetime]:
    """A new code for the user, replacing any earlier one. Only its digest is
    stored, and only until it expires."""
    client = get_redis()
    code = f"{secrets.randbelow(10**8):08d}"
    if previous := cast(bytes | str | None, client.get(user_key(user_id))):
        client.delete(code_key(previous.decode() if isinstance(previous, bytes) else previous))
    ttl = int(CODE_TTL.total_seconds())
    client.set(code_key(digest(code)), str(user_id), ex=ttl)
    client.set(user_key(user_id), digest(code), ex=ttl)
    return code, now() + CODE_TTL


def take_code(code: str) -> int | None:
    """The user a live code was issued to; the code is spent either way."""
    raw = cast(bytes | str | None, get_redis().getdel(code_key(digest(code))))
    return None if raw is None else int(raw)


def locked_out(author_id: int) -> bool:
    return int(cast(bytes | str | None, get_redis().get(failures_key(author_id))) or 0) >= MAX_FAILURES


def record_failure(author_id: int) -> None:
    pipe = get_redis().pipeline()
    pipe.incr(failures_key(author_id))
    pipe.expire(failures_key(author_id), FAILURE_WINDOW_SECONDS)
    pipe.execute()


def first_try(message_id: int) -> bool:
    """False for a message already handled: Discord redelivers pushes."""
    return bool(get_redis().set(seen_key(message_id), 1, nx=True, ex=FAILURE_WINDOW_SECONDS))


def confirm_prompt(bot_id: int, user_id: int) -> str | None:
    """What the bot asks before linking, or None when the code went to a bot
    not of the code's user."""
    with make_session() as session:
        bot = session.get_one(DiscordBot, bot_id)
        user = session.get_one(User, user_id)
        if bot.created_by_user_id != user_id:
            logger.warning(f"Discord link code of user {user_id} sent to bot {bot_id} of another user; code spent")
            return None
        person = user.person.display_name if user.person else user.name or user.email
        minutes = int(CONFIRM_TTL.total_seconds() // 60)
        return (
            f"This links this Discord account to Memory login {user.email} ({person}). "
            f"Reply `confirm` within {minutes} minutes to finish."
        )


def link(user_id: int, author_id: int) -> str | None:
    """Record that the author is the user; the reply to send, or None when refused."""
    with make_session() as session:
        user = session.get_one(User, user_id)
        person = link_discord_user(session, user, author_id)
        session.commit()
        login = f"Memory login {user.email}"
        if person is not None:
            return f"Linked to {person.display_name} ({login})."
        discord_user = session.get(DiscordUser, author_id)
        if discord_user is None or discord_user.verified_user_id != user_id:
            logger.warning(f"Discord user {author_id} not linked to user {user_id}: unknown or held by another user")
            return None
        return (
            f"Verified for {login}, but this Discord account is on another person record. "
            "An admin has to merge the two."
        )


def redeem(
    bot_id: int, channel_id: int, message_id: int, author_id: int, content: str, fresh: bool
) -> dict[str, Any] | None:
    """Handle a link message reaching storage. Only a ``fresh`` one (not an
    edit or history) in a DM is acted on; a code anywhere else is spent
    silently. None for any other message, which is stored as usual."""
    if not is_link_text(bot_id, author_id, content):
        return None
    with make_session() as session:
        channel = session.get(DiscordChannel, channel_id)
        direct = channel is not None and channel.is_direct
    if fresh and direct:
        return act_on_dm(bot_id, message_id, author_id, content)
    if spend_posted_code(content):
        return {"status": "skipped", "reason": "link_code_elsewhere"}
    return None  # a "confirm" outside a new DM is just a message


def redeem_dm(bot_id: int, message_id: int, author_id: int, author_name: str, content: str) -> dict[str, Any] | None:
    """Handle a new DM to the bot that Discord vouches for (the gateway, or a
    push read back with the bot's token), whether or not Memory collects that
    DM: a code must work from a stranger's first DM too. None when it isn't a
    link message. The author's DiscordUser is made if missing, as linking
    needs one; the DM channel isn't, so collection stays as it was."""
    if not is_link_text(bot_id, author_id, content):
        return None
    with make_session() as session:
        if session.get(DiscordUser, author_id) is None:
            session.add(DiscordUser(id=author_id, username=author_name))
            session.commit()
    return act_on_dm(bot_id, message_id, author_id, content)


def act_on_dm(bot_id: int, message_id: int, author_id: int, content: str) -> dict[str, Any]:
    if not first_try(message_id):
        return {"status": "skipped", "reason": "seen"}
    match = LINK_COMMAND.fullmatch(content)
    if match is None:
        return finish(bot_id, author_id)
    return try_code(bot_id, author_id, match[1])


def try_code(bot_id: int, author_id: int, code: str) -> dict[str, Any]:
    if locked_out(author_id):
        logger.warning(f"Discord user {author_id} locked out of link codes")
        return {"status": "skipped", "reason": "locked_out"}

    user_id = take_code(code)
    if user_id is None:
        record_failure(author_id)
        send_dm(bot_id, str(author_id), FAILED_REPLY)
        return {"status": "skipped", "reason": "bad_code"}
    prompt = confirm_prompt(bot_id, user_id)
    if prompt is None:
        send_dm(bot_id, str(author_id), FAILED_REPLY)
        return {"status": "skipped", "reason": "refused"}
    get_redis().set(pending_key(bot_id, author_id), str(user_id), ex=int(CONFIRM_TTL.total_seconds()))
    send_dm(bot_id, str(author_id), prompt)
    return {"status": "pending", "user_id": user_id}


def finish(bot_id: int, author_id: int) -> dict[str, Any]:
    """Link on the author's ``confirm``; the pending link is used up either way."""
    raw = cast(bytes | str | None, get_redis().getdel(pending_key(bot_id, author_id)))
    if raw is None:
        return {"status": "skipped", "reason": "nothing_pending"}
    user_id = int(raw)
    reply = link(user_id, author_id)
    if reply is None:
        send_dm(bot_id, str(author_id), FAILED_REPLY)
        return {"status": "skipped", "reason": "refused"}
    logger.info(f"Discord user {author_id} verified for user {user_id}")
    send_dm(bot_id, str(author_id), reply)
    return {"status": "verified", "user_id": user_id}
