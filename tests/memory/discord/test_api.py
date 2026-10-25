from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from memory.common.db.models import DiscordUser
from memory.discord.api import resolve_user_id


def guild_manager(*members):
    guild = SimpleNamespace(members=list(members))
    return SimpleNamespace(collectors={1: SimpleNamespace(guilds=[guild])})


@pytest.mark.asyncio
async def test_resolve_user_id_finds_a_stored_username(db_session):
    db_session.add(DiscordUser(id=111, username="alice", display_name="Alice Admin"))
    db_session.commit()

    assert await resolve_user_id("alice") == 111


@pytest.mark.asyncio
async def test_resolve_user_id_ignores_names_the_member_picks(db_session):
    db_session.add(DiscordUser(id=222, username="mallory", display_name="Alice Admin"))
    db_session.commit()
    mgr = guild_manager(
        SimpleNamespace(id=333, name="mallory2", nick="Alice Admin", global_name="Alice Admin")
    )

    with pytest.raises(HTTPException) as exc:
        await resolve_user_id("Alice Admin", mgr)  # type: ignore[arg-type]

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_user_id_finds_a_guild_member_by_username(db_session):
    mgr = guild_manager(SimpleNamespace(id=444, name="bob", nick=None, global_name=None))

    assert await resolve_user_id("bob", mgr) == 444  # type: ignore[arg-type]
