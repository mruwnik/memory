"""Storing what a Beeper Server lists: networks, chats, participants and messages.

Every write is an upsert keyed on Beeper's ids, so the backfill, the reconcile
and live events may store the same thing in any order, any number of times.
"""

import functools
import hashlib
import logging
import mimetypes
import pathlib
import re
from typing import Any, Callable
from urllib.parse import unquote

from markdownify import MarkdownConverter
from sqlalchemy import exc as sqlalchemy_exc

from memory.common import beeper, settings
from memory.common.content_processing import clear_item_chunks, process_content_item
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    BeeperAccount,
    BeeperChat,
    BeeperNetwork,
    BeeperParticipant,
    BeeperReaction,
)
from memory.common.db.models.source_items import BeeperMessage
from memory.common.db.upsert import insert_missing

logger = logging.getLogger(__name__)

# Media Server keeps for a chat. A file:// URL would serve any file on its disk.
ASSET_SCHEMES = ("mxc://", "localmxc://")

# A tag-shaped substring alone doesn't make text HTML ("if a<b and c>d", "use
# the <div> tag"), so converting takes markup that Matrix formatted bodies
# produce and plain text rarely holds.
HTML_TAG = re.compile(
    r"</?(?:a|b|i|u|s|p|br|hr|em|strong|del|code|pre|blockquote|ul|ol|li|h[1-6]|sup|sub|span|font|div"
    r"|table|thead|tbody|tr|th|td|caption|img|details|summary|mx-reply)\b[^<>]*>",
    re.IGNORECASE,
)
HTML_MARKUP = re.compile(
    r"<mx-reply>|<a\s+href="
    r"|<(a|b|i|u|s|p|em|strong|del|code|pre|blockquote|ul|ol|li|h[1-6]|sup|sub|span|font|div|table|details)"
    r"(?:\s[^<>]*)?>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
HTML_ENTITY = re.compile(r"&(?:lt|gt|amp|quot|#\d+|#x[0-9a-f]+);", re.IGNORECASE)
# The quoted parent of a reply: the reply-to is stored on its own.
MX_REPLY = re.compile(r"<mx-reply>.*?</mx-reply>", re.IGNORECASE | re.DOTALL)
MATRIX_USER = re.compile(r"https://matrix\.to/#/(@[^/?#]+)")
PILL = r'<a href="https://matrix\.to/#/[^"]*"[^>]*>[^<]*</a>'
# Slack's channel_join/channel_leave messages as Beeper bridges them: nothing
# in Server's fields marks them, so only a message that is nothing but this matches.
MEMBERSHIP_NOTICE = re.compile(
    rf"\s*{PILL} (?:has joined|has left|was added to|was removed from) the channel(?: by {PILL})?\s*"
)


def record_version(account: BeeperAccount, version: str | None) -> None:
    if version is None or version == account.server_version:
        return
    account.server_version = version
    if version != settings.BEEPER_SERVER_VERSION:
        logger.warning(
            "Beeper account %s: Server runs %s, expected %s",
            account.id, version, settings.BEEPER_SERVER_VERSION,
        )


# --- Networks, chats and participants ---


def resume_backfills(chats: list[BeeperChat]) -> None:
    for chat in chats:
        chat.backfill_stalls = 0


def refresh_networks(
    session: DBSession, account: BeeperAccount, accounts: list[beeper.NetworkAccount]
) -> list[BeeperNetwork]:
    """Record the networks connected in Beeper; new ones follow the account's
    collect default, ones no longer listed are marked removed, and ones
    connected again resume their chats' stopped backfills."""
    for entry in accounts:
        insert_missing(
            session,
            BeeperNetwork,
            account_id=account.id,
            network_account_id=entry.account_id,
            network=entry.network or entry.bridge.type,
            collect_messages=account.collect_by_default,
        )
    listed = {entry.account_id: entry for entry in accounts}
    networks = session.query(BeeperNetwork).filter_by(account_id=account.id).order_by(BeeperNetwork.id).all()
    for network in networks:
        entry = listed.get(network.network_account_id)
        if entry is None:
            network.status = "removed"
            continue
        network.network = entry.network or entry.bridge.type
        network.bridge_type = entry.bridge.type
        network.bridge_provider = entry.bridge.provider
        network.display_name = entry.user.display_text or entry.user.full_name or entry.user.username
        if entry.status == "connected" and network.status not in (None, "connected"):
            # Backfills likely stalled while the bridge was away.
            resume_backfills(network.chats)
        network.status = entry.status
    session.flush()
    return networks


def find_network(session: DBSession, account_id: int, network_account_id: str) -> BeeperNetwork | None:
    return (
        session.query(BeeperNetwork)
        .filter_by(account_id=account_id, network_account_id=network_account_id)
        .one_or_none()
    )


def find_chat(session: DBSession, account_id: int, chat_id: str) -> BeeperChat | None:
    return (
        session.query(BeeperChat)
        .join(BeeperNetwork, BeeperChat.network_id == BeeperNetwork.id)
        .filter(BeeperNetwork.account_id == account_id, BeeperChat.chat_id == chat_id)
        .one_or_none()
    )


def upsert_participant(
    session: DBSession,
    network: BeeperNetwork,
    participant_id: str,
    username: str | None = None,
    full_name: str | None = None,
    is_self: bool | None = None,
) -> BeeperParticipant:
    insert_missing(session, BeeperParticipant, network_id=network.id, participant_id=participant_id)
    participant = (
        session.query(BeeperParticipant).filter_by(network_id=network.id, participant_id=participant_id).one()
    )
    participant.username = username or participant.username
    participant.full_name = full_name or participant.full_name
    if is_self is not None:
        participant.is_self = is_self
    return participant


def upsert_chat(session: DBSession, account: BeeperAccount, chat: beeper.Chat) -> BeeperChat | None:
    """Record a chat and the participants it lists; None when its network isn't
    one of this account's (refresh the networks first)."""
    network = find_network(session, account.id, chat.account_id)
    if network is None:
        return None
    insert_missing(session, BeeperChat, network_id=network.id, chat_id=chat.id)
    row = session.query(BeeperChat).filter_by(network_id=network.id, chat_id=chat.id).one()
    row.local_chat_id = chat.local_chat_id
    row.title = chat.title
    row.chat_type = chat.type
    row.is_merged = chat.merge is not None
    row.merged_into = chat.merged_into_chat_id
    row.message_expiry_seconds = chat.message_expiry_seconds
    row.last_activity_at = chat.last_activity
    for person in chat.participants.items:
        upsert_participant(session, network, person.id, person.username, person.full_name, person.is_self)
    return row


# --- Messages ---


def asset_url(attachment: beeper.Attachment) -> str | None:
    url = attachment.src_url or attachment.id
    return url if url and url.startswith(ASSET_SCHEMES) else None


def image_path(chat_pk: int, attachment: beeper.Attachment) -> pathlib.Path:
    """Where an image is kept: named by its URL, typed by its mimeType."""
    digest = hashlib.sha256((asset_url(attachment) or "").encode()).hexdigest()[:32]
    extension = mimetypes.guess_extension(attachment.mime_type or "") or ".bin"
    return settings.BEEPER_STORAGE_DIR / str(chat_pk) / f"{digest}{extension}"


def images_to_fetch(message: beeper.Message) -> list[beeper.Attachment]:
    return [
        a for a in message.attachments if (a.mime_type or "").startswith("image/") and asset_url(a) is not None
    ]


NameOf = Callable[[str], str | None]


class ChatTextConverter(MarkdownConverter):
    """Markdown as email HTML gets, with Matrix mention links as ``@Name``."""

    def __init__(self, name_of: NameOf):
        super().__init__(escape_underscores=False, escape_asterisks=False)
        self.name_of = name_of

    def convert_a(self, el, text, convert_as_inline):
        user = MATRIX_USER.fullmatch(unquote(el.get("href") or ""))
        if user is None:
            return super().convert_a(el, text, convert_as_inline)
        user_id = user.group(1)
        name = text.strip() or self.name_of(user_id) or user_id[1:].split(":")[0]
        return name if name.startswith("@") else f"@{name}"

    def convert_img(self, el, text, convert_as_inline):
        # Media comes in as attachments; the inline mxc:// reference is noise.
        if (el.get("src") or "").startswith(ASSET_SCHEMES):
            return ""
        return super().convert_img(el, text, convert_as_inline)


def is_html(text: str) -> bool:
    # Formatted bodies escape a literal "<" as &lt;, so a bare one means plain text.
    if not all(HTML_TAG.match(text, i) for i, char in enumerate(text) if char == "<"):
        return False
    return bool(HTML_MARKUP.search(text) or (HTML_ENTITY.search(text) and HTML_TAG.search(text)))


def plain_text(text: str, name_of: NameOf) -> str:
    """Formatted (HTML) message text as plain text; plain text unchanged."""
    if not is_html(text):
        return text
    markdown = ChatTextConverter(name_of).convert(MX_REPLY.sub("", text))
    lines = (line.rstrip() for line in markdown.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def is_membership_notice(text: str | None) -> bool:
    return text is not None and MEMBERSHIP_NOTICE.fullmatch(text) is not None


def participant_name(session: DBSession, chat_pk: int, participant_id: str) -> str | None:
    """The name of someone on the chat's network, if memory has seen them."""
    participant = (
        session.query(BeeperParticipant)
        .join(BeeperChat, BeeperChat.network_id == BeeperParticipant.network_id)
        .filter(BeeperChat.id == chat_pk, BeeperParticipant.participant_id == participant_id)
        .one_or_none()
    )
    return participant and (participant.full_name or participant.username)


def names_in_chat(session: DBSession, chat_pk: int) -> NameOf:
    return functools.partial(participant_name, session, chat_pk)


def message_content(message: beeper.Message, name_of: NameOf) -> str:
    """The text as plain text, with voice-note transcriptions after it."""
    transcripts = [a.transcription.transcription for a in message.attachments if a.transcription]
    plain = plain_text(message.text, name_of) if message.text else None
    return "\n\n".join(filter(None, [plain, *transcripts]))


def reactions_by_key(message: beeper.Message) -> dict[str, list[str]] | None:
    grouped: dict[str, set[str]] = {}
    for reaction in message.reactions:
        grouped.setdefault(reaction.reaction_key, set()).add(reaction.participant_id)
    return {key: sorted(ids) for key, ids in grouped.items()} or None


def attachment_record(attachment: beeper.Attachment) -> dict[str, Any]:
    return {
        "id": attachment.id,
        "type": attachment.type,
        "mime_type": attachment.mime_type,
        "file_name": attachment.file_name,
        "file_size": attachment.file_size,
        "is_voice_note": attachment.is_voice_note,
        "duration": attachment.duration,
        "transcription": attachment.transcription.transcription if attachment.transcription else None,
    }


def message_fields(message: beeper.Message, images: list[str]) -> dict[str, Any]:
    return {
        "sort_key": message.sort_key,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "is_sender": message.is_sender,
        "sent_at": message.timestamp,
        "edited_at": message.edited_timestamp,
        "reply_to_message_id": message.linked_message_id,
        "message_type": message.type or "TEXT",
        "mentions": message.mentions,
        "reactions": reactions_by_key(message),
        "attachments": [attachment_record(a) for a in message.attachments] or None,
        "images": images or None,
    }


def find_message(
    session: DBSession, chat_pk: int, message_id: str, event_id: str | None = None
) -> BeeperMessage | None:
    """The stored message, matched on its Matrix event id first: that survives
    a rebuilt Server, whose per-install ids all change."""
    query = session.query(BeeperMessage).filter_by(chat_id=chat_pk)
    if event_id is not None and (found := query.filter_by(event_id=event_id).first()) is not None:
        return found
    return query.filter_by(message_id=message_id).one_or_none()


def delete_message(session: DBSession, chat_pk: int, message_id: str, event_id: str | None = None) -> str:
    session.query(BeeperReaction).filter_by(chat_id=chat_pk, message_id=message_id).delete()
    stored = find_message(session, chat_pk, message_id, event_id)
    if stored is None:
        session.commit()
        return "ignored"
    clear_item_chunks(stored, session)
    session.delete(stored)
    session.commit()
    return "deleted"


def forget_message(session: DBSession, chat_pk: int, message_id: str) -> str | None:
    """Server no longer has this message (an id or a Matrix event id): delete the
    stored copy. Returns the parent's id when it was a reaction, to refresh."""
    reaction = session.get(BeeperReaction, (chat_pk, message_id))
    if reaction is None:
        delete_message(session, chat_pk, message_id, event_id=message_id)
        return None
    parent = reaction.parent_message_id
    session.delete(reaction)
    session.commit()
    return parent


def record_reaction(session: DBSession, chat_pk: int, message: beeper.Message) -> str:
    if message.linked_message_id:
        insert_missing(
            session, BeeperReaction, chat_id=chat_pk, message_id=message.id,
            parent_message_id=message.linked_message_id,
        )
        session.commit()
    return "ignored"


def is_older(message: beeper.Message, stored: BeeperMessage) -> bool:
    """Edits can arrive out of order, and an unedited copy is older than any edit."""
    if stored.edited_at is None:
        return False
    return message.edited_timestamp is None or message.edited_timestamp < stored.edited_at


# Fields whose change, with the text unchanged, makes a message "updated".
UPDATES = ("edited_at", "reactions", "attachments")


def store_message(
    session: DBSession, chat: BeeperChat, message: beeper.Message, images: list[str], event_id: str | None = None
) -> str:
    """Store, edit or delete one message as Server now lists it; returns what was done.

    A REACTION row is never stored: its parent lists the reactions, and only
    which message it reacts to is kept. Hidden rows (state events) aren't stored either.
    """
    if message.is_deleted:
        return delete_message(session, chat.id, message.id, event_id)
    if message.type == "REACTION":
        return record_reaction(session, chat.id, message)
    if message.is_hidden:
        return "ignored"
    if is_membership_notice(message.text):
        return delete_message(session, chat.id, message.id, event_id)

    upsert_participant(session, chat.network, message.sender_id, full_name=message.sender_name)
    content = message_content(message, names_in_chat(session, chat.id))
    fields = message_fields(message, images)
    stored = find_message(session, chat.id, message.id, event_id)
    if stored is None:
        added = add_message(session, chat, message, content, fields, event_id)
        if added is not None:
            return added
        stored = find_message(session, chat.id, message.id, event_id)
        if stored is None:
            return "ignored"

    if is_older(message, stored):
        return "unchanged"
    updated = any(getattr(stored, name) != fields[name] for name in UPDATES)
    stored.message_id = message.id
    stored.event_id = event_id or stored.event_id
    for name, value in fields.items():
        setattr(stored, name, value)
    if stored.content == content:
        session.commit()
        return "updated" if updated else "unchanged"
    stored.content = content
    clear_item_chunks(stored, session)
    process_content_item(stored, session)
    return "edited"


def add_message(
    session: DBSession,
    chat: BeeperChat,
    message: beeper.Message,
    content: str,
    fields: dict[str, Any],
    event_id: str | None,
) -> str | None:
    """Insert a new message; None when another worker stored it first."""
    item = BeeperMessage(
        modality="message",
        sha256=hashlib.sha256(f"beeper:{chat.id}:{message.id}".encode()).digest(),
        content=content,
        chat_id=chat.id,
        message_id=message.id,
        event_id=event_id,
        **fields,
    )
    session.add(item)
    try:
        session.flush()
    except sqlalchemy_exc.IntegrityError:
        # The error text holds the row's content, so only ids are logged.
        session.rollback()
        logger.info("Beeper message %s in chat %s already stored", message.id, chat.id)
        return None
    # The sha256 check drops an item another worker already committed.
    if item not in session:
        return None
    process_content_item(item, session)
    return "added"
