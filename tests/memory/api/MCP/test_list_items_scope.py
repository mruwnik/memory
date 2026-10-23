# pyright: reportFunctionMemberAccess=false
from datetime import datetime, timedelta, timezone

import pytest

from memory.api.MCP.servers.core import count_items, list_items
from memory.common.db.models import (
    BlogPost,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    ForumPost,
    GithubItem,
    MailMessage,
    SlackChannel,
    SlackMessage,
    SlackWorkspace,
)
from tests.conftest import mcp_auth_context, unique_sha256

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
STORED = {"embed_status": "STORED", "tags": []}


@pytest.fixture
def corpus(db_session):
    db_session.add_all(
        [
            DiscordServer(id=1, name="s"),
            DiscordUser(id=100, username="alice", display_name="Alice A"),
            DiscordUser(id=101, username="bob"),
            SlackWorkspace(id="T1", name="w"),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            DiscordChannel(id=10, server_id=1, name="general", channel_type="text"),
            DiscordChannel(id=11, server_id=1, name="random", channel_type="text"),
            SlackChannel(id="C1", workspace_id="T1", name="eng", channel_type="channel"),
        ]
    )
    db_session.flush()

    def discord(n, channel, author, days_ago):
        return DiscordMessage(
            sha256=unique_sha256(f"d{n}"),
            modality="message",
            content=f"discord {n}",
            message_id=n,
            channel_id=channel,
            server_id=1,
            author_id=author,
            sent_at=NOW - timedelta(days=days_ago),
            **STORED,
        )

    items = {
        "d_general_alice_old": discord(1, 10, 100, 30),
        "d_general_bob_new": discord(2, 10, 101, 1),
        "d_random_alice_new": discord(3, 11, 100, 2),
        "slack_eng_carol": SlackMessage(
            sha256=unique_sha256("s"),
            modality="message",
            content="slack",
            message_ts=str((NOW - timedelta(days=1)).timestamp()),
            channel_id="C1",
            workspace_id="T1",
            author_id="U9",
            author_name="carol",
            **STORED,
        ),
        "mail_from_alice": MailMessage(
            sha256=unique_sha256("m"),
            modality="mail",
            content="mail",
            message_id="<m@x>",
            sender="Alice <alice@example.com>",
            sent_at=NOW - timedelta(days=3),
            **STORED,
        ),
        "gh_memory_alice": GithubItem(
            sha256=unique_sha256("g"),
            modality="github",
            content="issue",
            kind="issue",
            repo_path="equistamp/memory",
            number=1,
            author="alice",
            created_at=NOW - timedelta(days=5),
            **STORED,
        ),
        "forum_by_dave": ForumPost(
            sha256=unique_sha256("f"),
            modality="forum",
            content="forum",
            url="https://f/1",
            authors=["Dave"],
            inserted_at=NOW - timedelta(days=40),
            **STORED,
        ),
        "blog_undated": BlogPost(
            sha256=unique_sha256("b"),
            modality="blog",
            content="blog",
            url="https://x/1",
            inserted_at=NOW - timedelta(days=4),
            **STORED,
        ),
    }
    db_session.add_all(items.values())
    db_session.commit()
    return items


async def listed(session_id, **kwargs):
    with mcp_auth_context(session_id):
        result = await list_items.fn(include_metadata=False, limit=200, **kwargs)
        counted = await count_items.fn(**kwargs)
    assert counted["total"] == result["total"]
    return {i["id"] for i in result["items"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"channel": "general"}, {"d_general_alice_old", "d_general_bob_new"}),
        ({"channel": "#general"}, {"d_general_alice_old", "d_general_bob_new"}),
        ({"channel": "11"}, {"d_random_alice_new"}),
        ({"channel": "eng"}, {"slack_eng_carol"}),
        ({"channel": "C1"}, {"slack_eng_carol"}),
        (
            {"author": "ALICE"},
            {"d_general_alice_old", "d_random_alice_new", "mail_from_alice", "gh_memory_alice"},
        ),
        ({"author": "carol"}, {"slack_eng_carol"}),
        ({"repo": "EquiStamp/Memory"}, {"gh_memory_alice"}),
        ({"author": "DAVE"}, {"forum_by_dave"}),
        # A date-only until includes that whole day.
        (
            {"since": "2026-10-17", "until": "2026-10-17"},
            {"mail_from_alice"},
        ),
        ({"channel": "general", "author": "alice"}, {"d_general_alice_old"}),
        (
            {"since": (NOW - timedelta(days=2, hours=1)).isoformat()},
            {"d_general_bob_new", "d_random_alice_new", "slack_eng_carol"},
        ),
        (
            {
                "since": (NOW - timedelta(days=6)).isoformat(),
                "until": (NOW - timedelta(days=2, hours=12)).isoformat(),
            },
            {"mail_from_alice", "gh_memory_alice", "blog_undated"},
        ),
    ],
)
async def test_list_items_scopes(db_session, admin_session, corpus, kwargs, expected):
    got = await listed(admin_session.id, **kwargs)

    assert got == {corpus[name].id for name in expected}


@pytest.mark.asyncio
async def test_scoping_never_widens_access(db_session, user_session, corpus):
    assert await listed(user_session.id, channel="general") == set()


@pytest.mark.asyncio
async def test_bad_dates_are_refused(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="since"):
            await list_items.fn(since="last tuesday")
