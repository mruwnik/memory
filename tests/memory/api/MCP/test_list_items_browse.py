# pyright: reportFunctionMemberAccess=false
from datetime import datetime, timedelta, timezone

import pytest

from memory.api.MCP.servers.core import count_items, list_items
from memory.common.db.models import (
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    EmailAccount,
    GithubIssue,
    MailMessage,
    MiscDoc,
)
from tests.conftest import mcp_auth_context, unique_sha256

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
ALL_STATUSES = {"RAW", "QUEUED", "STORED", "FAILED", "SKIPPED"}
# Discord snowflakes overflow a JavaScript number, so connection ids are strings.
BIG_CHANNEL = 1234567890123456789


def email_account(user_id, address):
    return EmailAccount(
        user_id=user_id,
        name=address,
        email_address=address,
        imap_server="imap.example.com",
        imap_port=993,
        username=address,
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
    )


def mail(name, account, subject, sender, days_ago, **kwargs):
    return MailMessage(
        sha256=unique_sha256(name),
        modality="mail",
        content=f"body of {name} with zebra",
        message_id=f"<{name}@x>",
        subject=subject,
        sender=sender,
        email_account_id=account.id,
        inserted_at=NOW - timedelta(days=days_ago),
        tags=[],
        **({"embed_status": "STORED"} | kwargs),
    )


@pytest.fixture
def corpus(db_session, admin_user, regular_user):
    work = email_account(admin_user.id, "work@example.com")
    home = email_account(admin_user.id, "home@example.com")
    db_session.add_all(
        [
            work,
            home,
            DiscordServer(id=1, name="s"),
            DiscordUser(id=100, username="alice"),
        ]
    )
    db_session.flush()
    db_session.add(
        DiscordChannel(id=BIG_CHANNEL, server_id=1, name="general", channel_type="text")
    )
    db_session.flush()
    items = {
        "work_invoice": mail(
            "wi", work, "Invoice 100% paid", "Billing <bill@acme.io>", 1
        ),
        "work_lunch": mail("wl", work, "Lunch plans", "Bob <bob@example.com>", 2),
        "home_failed": mail(
            "hf",
            home,
            "Holiday photos",
            "Mum <mum@example.com>",
            3,
            embed_status="FAILED",
        ),
        "home_invoice": mail("hi", home, "invoice reminder", "Shop <s@shop.io>", 4),
        "discord": DiscordMessage(
            sha256=unique_sha256("d"),
            modality="message",
            content="invoice talk",
            message_id=1,
            channel_id=BIG_CHANNEL,
            server_id=1,
            author_id=100,
            sent_at=NOW,
            inserted_at=NOW - timedelta(days=5),
            embed_status="STORED",
            tags=[],
        ),
        "issue": GithubIssue(
            sha256=unique_sha256("g"),
            modality="github",
            content="issue body",
            kind="issue",
            repo_path="acme/billing",
            number=7,
            title="Invoices render wrong",
            author="carol",
            inserted_at=NOW - timedelta(days=6),
            embed_status="STORED",
            tags=[],
        ),
        "doc": MiscDoc(
            sha256=unique_sha256("m"),
            modality="doc",
            content="plain",
            filename="reports/INVOICE_2026.pdf",
            inserted_at=NOW - timedelta(days=7),
            embed_status="STORED",
            tags=[],
        ),
        "user_doc": MiscDoc(
            sha256=unique_sha256("u"),
            modality="doc",
            content="mine",
            filename="notes/user_invoice.txt",
            creator_id=regular_user.id,
            inserted_at=NOW - timedelta(days=8),
            embed_status="STORED",
            tags=[],
        ),
    }
    db_session.add_all(items.values())
    db_session.commit()
    return {"items": items, "work": work, "home": home}


def names_of(corpus, result):
    by_id = {item.id: name for name, item in corpus["items"].items()}
    return [by_id[row["id"]] for row in result["items"]]


async def browse(session, **kwargs):
    with mcp_auth_context(session.token):
        result = await list_items.fn(include_metadata=False, **kwargs)
        counted = await count_items.fn(
            **{k: v for k, v in kwargs.items() if k not in ("limit", "offset")}
        )
    return result, counted


@pytest.mark.asyncio
async def test_lists_newest_first_with_type_and_status(
    db_session, admin_session, corpus
):
    result, counted = await browse(admin_session, embed_statuses=set())

    assert names_of(corpus, result) == [
        "work_invoice",
        "work_lunch",
        "home_failed",
        "home_invoice",
        "discord",
        "issue",
        "doc",
        "user_doc",
    ]
    assert result["total"] == counted["total"] == 8
    assert result["items"][2]["type"] == "mail_message"
    assert result["items"][2]["embed_status"] == "FAILED"
    assert counted["by_type"] == {
        "mail_message": 4,
        "discord_message": 1,
        "github_issue": 1,
        "misc_doc": 2,
    }


@pytest.mark.asyncio
async def test_regular_user_sees_only_accessible_items(
    db_session, user_session, corpus
):
    result, counted = await browse(
        user_session, embed_statuses=set(), contains="invoice"
    )

    assert names_of(corpus, result) == ["user_doc"]
    assert counted["total"] == 1
    assert counted["by_type"] == {"misc_doc": 1}


@pytest.mark.asyncio
async def test_regular_user_sees_no_connections_of_others(
    db_session, user_session, corpus
):
    _, counted = await browse(
        user_session, item_types={"mail_message"}, embed_statuses=set()
    )

    assert counted["by_connection"] == []


@pytest.fixture
def user_owns_work_lunch(db_session, regular_user, corpus):
    """The regular user can see one mail in the admin's work account, nothing in home."""
    corpus["items"]["work_lunch"].creator_id = regular_user.id
    db_session.commit()
    return corpus


@pytest.mark.asyncio
async def test_regular_user_counts_only_visible_connection_items(
    db_session, user_session, user_owns_work_lunch
):
    _, counted = await browse(
        user_session, item_types={"mail_message"}, embed_statuses=set()
    )

    work = user_owns_work_lunch["work"]
    assert counted["by_connection"] == [
        {"id": str(work.id), "name": "work@example.com", "count": 1}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("account, expected", [("work", ["work_lunch"]), ("home", [])])
async def test_regular_user_lists_only_visible_items_of_a_connection(
    db_session, user_session, user_owns_work_lunch, account, expected
):
    result, counted = await browse(
        user_session,
        item_types={"mail_message"},
        embed_statuses=set(),
        connection_id=str(user_owns_work_lunch[account].id),
    )

    assert names_of(user_owns_work_lunch, result) == expected
    assert counted["total"] == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset, expected, has_more",
    [
        (0, ["work_invoice", "work_lunch", "home_failed"], True),
        (3, ["home_invoice", "discord", "issue"], True),
        (6, ["doc", "user_doc"], False),
        (8, [], False),
    ],
)
async def test_pages_through_everything(
    db_session, admin_session, corpus, offset, expected, has_more
):
    with mcp_auth_context(admin_session.token):
        result = await list_items.fn(
            embed_statuses=set(), limit=3, offset=offset, include_total=False
        )

    assert names_of(corpus, result) == expected
    assert result["has_more"] is has_more
    assert result["total"] is None


@pytest.mark.asyncio
async def test_ties_on_inserted_at_page_by_id(db_session, admin_session):
    same = datetime(2026, 1, 1, tzinfo=timezone.utc)
    docs = [
        MiscDoc(
            sha256=unique_sha256(f"t{n}"),
            modality="doc",
            content="x",
            inserted_at=same,
            embed_status="STORED",
            tags=[],
        )
        for n in range(5)
    ]
    db_session.add_all(docs)
    db_session.commit()

    with mcp_auth_context(admin_session.token):
        pages = [
            await list_items.fn(include_metadata=False, limit=2, offset=offset)
            for offset in (0, 2, 4)
        ]

    listed = [row["id"] for page in pages for row in page["items"]]
    assert listed == sorted((doc.id for doc in docs), reverse=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contains, expected",
    [
        # Subjects, GitHub titles and filenames, case-insensitively.
        ("INVOICE", ["work_invoice", "home_invoice", "issue", "doc", "user_doc"]),
        # Senders.
        ("acme.io", ["work_invoice"]),
        ("carol", ["issue"]),
        ("acme/billing", ["issue"]),
        # LIKE wildcards are literal.
        ("100%", ["work_invoice"]),
        ("0%p", []),
        ("s_u", []),
        # Body text is not searched.
        ("zebra", []),
        (
            "   ",
            [
                "work_invoice",
                "work_lunch",
                "home_invoice",
                "discord",
                "issue",
                "doc",
                "user_doc",
            ],
        ),
    ],
)
async def test_text_search(db_session, admin_session, corpus, contains, expected):
    result, counted = await browse(admin_session, contains=contains)

    assert names_of(corpus, result) == expected
    assert counted["total"] == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (
            {"item_types": {"mail_message"}},
            ["work_invoice", "work_lunch", "home_invoice"],
        ),
        (
            {"item_types": {"mail_message"}, "embed_statuses": set()},
            ["work_invoice", "work_lunch", "home_failed", "home_invoice"],
        ),
        (
            {"item_types": {"mail_message"}, "embed_statuses": {"FAILED"}},
            ["home_failed"],
        ),
        ({"item_types": {"github_issue", "misc_doc"}}, ["issue", "doc", "user_doc"]),
        (
            {"item_types": {"mail_message"}, "contains": "invoice"},
            ["work_invoice", "home_invoice"],
        ),
    ],
)
async def test_filters_by_item_type(
    db_session, admin_session, corpus, kwargs, expected
):
    result, _ = await browse(admin_session, **kwargs)

    assert names_of(corpus, result) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account, contains, expected",
    [
        ("work", None, ["work_invoice", "work_lunch"]),
        ("home", None, ["home_failed", "home_invoice"]),
        ("home", "invoice", ["home_invoice"]),
    ],
)
async def test_filters_by_email_account(
    db_session, admin_session, corpus, account, contains, expected
):
    result, _ = await browse(
        admin_session,
        item_types={"mail_message"},
        embed_statuses=set(),
        connection_id=str(corpus[account].id),
        contains=contains,
    )

    assert names_of(corpus, result) == expected


@pytest.mark.asyncio
async def test_counts_items_per_connection(db_session, admin_session, corpus):
    _, mail_counts = await browse(
        admin_session, item_types={"mail_message"}, embed_statuses=set()
    )
    _, discord_counts = await browse(admin_session, item_types={"discord_message"})

    assert mail_counts["by_connection"] == [
        {"id": str(corpus["home"].id), "name": "home@example.com", "count": 2},
        {"id": str(corpus["work"].id), "name": "work@example.com", "count": 2},
    ]
    assert discord_counts["by_connection"] == [
        {"id": str(BIG_CHANNEL), "name": "general", "count": 1}
    ]


@pytest.mark.asyncio
async def test_filters_by_discord_channel_id_string(db_session, admin_session, corpus):
    result, _ = await browse(
        admin_session, item_types={"discord_message"}, connection_id=str(BIG_CHANNEL)
    )

    assert names_of(corpus, result) == ["discord"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item_types", [set(), {"misc_doc"}, {"mail_message", "discord_message"}]
)
async def test_no_connection_breakdown_without_one_connection_kind(
    db_session, admin_session, corpus, item_types
):
    _, counted = await browse(admin_session, item_types=item_types)

    assert counted["by_connection"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"embed_statuses": {"DONE"}}, "embed_statuses"),
        ({"connection_id": "1"}, "connection_id"),
        (
            {"item_types": {"mail_message", "discord_message"}, "connection_id": "1"},
            "connection_id",
        ),
        ({"item_types": {"mail_message"}, "connection_id": "abc"}, "integer"),
    ],
)
async def test_bad_browse_arguments_are_refused(
    db_session, admin_session, kwargs, message
):
    with mcp_auth_context(admin_session.token):
        with pytest.raises(ValueError, match=message):
            await list_items.fn(**kwargs)
        with pytest.raises(ValueError, match=message):
            await count_items.fn(**kwargs)
