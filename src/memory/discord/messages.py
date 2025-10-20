import logging
import textwrap
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy.orm import Session, scoped_session

from memory.common.db.models import (
    DiscordChannel,
    DiscordUser,
    ScheduledLLMCall,
    DiscordMessage,
)

logger = logging.getLogger(__name__)

DiscordEntity = DiscordChannel | DiscordUser | str | int | None


def resolve_discord_user(
    session: Session | scoped_session, entity: DiscordEntity
) -> DiscordUser | None:
    if not entity:
        return None
    if isinstance(entity, DiscordUser):
        return entity
    if isinstance(entity, int):
        return session.get(DiscordUser, entity)

    return session.query(DiscordUser).filter(DiscordUser.username == entity).first()


def resolve_discord_channel(
    session: Session | scoped_session, entity: DiscordEntity
) -> DiscordChannel | None:
    if not entity:
        return None
    if isinstance(entity, DiscordChannel):
        return entity
    if isinstance(entity, int):
        return session.get(DiscordChannel, entity)

    return session.query(DiscordChannel).filter(DiscordChannel.name == entity).first()


def schedule_discord_message(
    session: Session | scoped_session,
    scheduled_time: datetime,
    message: str,
    user_id: int,
    model: str | None = None,
    topic: str | None = None,
    discord_user: DiscordEntity = None,
    discord_channel: DiscordEntity = None,
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ScheduledLLMCall:
    discord_user = resolve_discord_user(session, discord_user)
    discord_channel = resolve_discord_channel(session, discord_channel)
    if not discord_user and not discord_channel:
        raise ValueError("Either discord_user or discord_channel must be provided")

    # Validate that the scheduled time is in the future
    # Compare with naive datetime since we store naive in the database
    current_time_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if scheduled_time.replace(tzinfo=None) <= current_time_naive:
        raise ValueError("Scheduled time must be in the future")

    # Create the scheduled call
    scheduled_call = ScheduledLLMCall(
        user_id=user_id,
        scheduled_time=scheduled_time,
        message=message,
        topic=topic,
        model=model,
        system_prompt=system_prompt,
        discord_channel=resolve_discord_channel(session, discord_channel),
        discord_user=resolve_discord_user(session, discord_user),
        data=metadata or {},
    )

    session.add(scheduled_call)
    return scheduled_call


def upsert_scheduled_message(
    session: Session | scoped_session,
    scheduled_time: datetime,
    message: str,
    user_id: int,
    model: str | None = None,
    topic: str | None = None,
    discord_user: DiscordEntity = None,
    discord_channel: DiscordEntity = None,
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ScheduledLLMCall:
    discord_user = resolve_discord_user(session, discord_user)
    discord_channel = resolve_discord_channel(session, discord_channel)
    prev_call = (
        session.query(ScheduledLLMCall)
        .filter(
            ScheduledLLMCall.user_id == user_id,
            ScheduledLLMCall.model == model,
            ScheduledLLMCall.discord_user_id == (discord_user and discord_user.id),
            ScheduledLLMCall.discord_channel_id
            == (discord_channel and discord_channel.id),
        )
        .first()
    )
    naive_scheduled_time = scheduled_time.replace(tzinfo=None)
    print(f"naive_scheduled_time: {naive_scheduled_time}")
    print(f"prev_call.scheduled_time: {prev_call and prev_call.scheduled_time}")
    if prev_call and cast(datetime, prev_call.scheduled_time) > naive_scheduled_time:
        prev_call.status = "cancelled"  # type: ignore

    return schedule_discord_message(
        session,
        scheduled_time,
        message,
        user_id=user_id,
        model=model,
        topic=topic,
        discord_user=discord_user,
        discord_channel=discord_channel,
        system_prompt=system_prompt,
        metadata=metadata,
    )


def previous_messages(
    session: Session | scoped_session,
    user_id: int | None,
    channel_id: int | None,
    max_messages: int = 10,
    offset: int = 0,
) -> list[DiscordMessage]:
    messages = session.query(DiscordMessage)
    if user_id:
        messages = messages.filter(DiscordMessage.recipient_id == user_id)
    if channel_id:
        messages = messages.filter(DiscordMessage.channel_id == channel_id)
    return list(
        reversed(
            messages.order_by(DiscordMessage.sent_at.desc())
            .offset(offset)
            .limit(max_messages)
            .all()
        )
    )


def comm_channel_prompt(
    session: Session | scoped_session,
    user: DiscordEntity,
    channel: DiscordEntity,
    max_messages: int = 10,
) -> str:
    user = resolve_discord_user(session, user)
    channel = resolve_discord_channel(session, channel)

    messages = previous_messages(
        session, user and user.id, channel and channel.id, max_messages
    )

    server_context = ""
    if channel and channel.server:
        server_context = textwrap.dedent("""
            Here are your previous notes on the server:
            <server_context>
            {summary}
            </server_context>
        """).format(summary=channel.server.summary)
    if channel:
        server_context += textwrap.dedent("""
            Here are your previous notes on the channel:
            <channel_context>
            {summary}
            </channel_context>
        """).format(summary=channel.summary)
    if messages:
        server_context += textwrap.dedent("""
            Here are your previous notes on the users:
            <user_notes>
            {users}
            </user_notes>
        """).format(
            users="\n".join({msg.from_user.as_xml() for msg in messages}),
        )

    return textwrap.dedent("""
        You are a bot communicating on Discord.

        {server_context}

        Whenever something worth remembering is said, you should add a note to the appropriate context - use
        this to track your understanding of the conversation and those taking part in it.

        You will be given the last {max_messages} messages in the conversation.
        Please react to them appropriately. You can return an empty response if you don't have anything to say.
    """).format(server_context=server_context, max_messages=max_messages)
