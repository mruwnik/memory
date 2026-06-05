"""Tests for the deadlines MCP server."""

from datetime import date, timedelta

import pytest

from memory.api.MCP.servers.deadlines import (
    accessible_source_items,
    attach,
    delete,
    detach,
    fetch,
    list_upcoming,
    upsert,
)
from memory.common.content_processing import create_content_hash
from memory.common.db import connection as db_connection
from memory.common.db.models import Deadline, Person, SourceItem, Team
from memory.common.db.models.sources import Project, project_teams, team_members
from tests.conftest import mcp_auth_context


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped."""
    return getattr(tool, "fn", tool)


# Thin shims so the tests can keep their previously-validated call shapes.
# Backed entirely by the public `upsert` tool:
#   - create(...)            == upsert(deadline_id=None, ...)
#   - update(deadline_id, ...) == upsert(deadline_id=<id>, ...)
async def _create(**kwargs):
    return await get_fn(upsert)(**kwargs)


async def _update(deadline_id, **kwargs):
    return await get_fn(upsert)(deadline_id=deadline_id, **kwargs)


# Wrap the shims in objects with `.fn` so the existing `get_fn(...)` callsites
# unwrap to the right thing. This is the cheapest way to keep tests readable
# without touching every call site.
class _Shim:
    def __init__(self, fn):
        self.fn = fn


create = _Shim(_create)
update = _Shim(_update)


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.fixture
def two_source_items(db_session):
    items = [
        SourceItem(
            modality="text",
            sha256=create_content_hash(f"item-{i}"),
            content=f"item-{i}",
            sensitivity="basic",
        )
        for i in range(2)
    ]
    db_session.add_all(items)
    db_session.commit()
    for item in items:
        db_session.refresh(item)
    return items


def test_accessible_source_items_excludes_hidden_for_admin(db_session, admin_user):
    """accessible_source_items drops "hidden" items even for admins.

    Keeps the deadline attach/upsert paths from leaking the existence of hidden
    content (the accessible/denied counts) to an admin.
    """
    visible = SourceItem(
        modality="text",
        sha256=create_content_hash("deadline-visible"),
        content="visible",
        sensitivity="basic",
    )
    hidden = SourceItem(
        modality="text",
        sha256=create_content_hash("deadline-hidden"),
        content="hidden",
        sensitivity="hidden",
    )
    db_session.add_all([visible, hidden])
    db_session.commit()

    result = accessible_source_items(db_session, admin_user, [visible.id, hidden.id])

    assert [item.id for item in result] == [visible.id]


@pytest.fixture
def regular_user_other_creator_item(db_session):
    """A SourceItem the regular_user did NOT create — used to test AC.

    Inaccessibility comes from the NULL-project rule (unclassified content
    is not visible to regular users), not from `sensitivity`. Keeping the
    sensitivity at `basic` makes the failing access path unambiguous: it's
    the missing project, not the high sensitivity, that hides the row.
    """
    item = SourceItem(
        modality="text",
        sha256=create_content_hash("private to admin"),
        content="private to admin",
        sensitivity="basic",
        creator_id=None,  # Unowned, project-less; only admins see it
        project_id=None,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_create(db_session, admin_user, admin_session):
    with mcp_auth_context(admin_session.id):
        result = await get_fn(create)(
            title="Grant: NSF #1234",
            date="2026-06-30",
            priority="urgent",
            description="Submit by 5pm Eastern",
            tags=["grant"],
        )

    assert result["title"] == "Grant: NSF #1234"
    assert result["date"] == "2026-06-30"
    assert result["priority"] == "urgent"
    assert result["creator_id"] == admin_user.id
    assert result["tags"] == ["grant"]


@pytest.mark.asyncio
async def test_create_invalid_date_rejected(
    db_session, admin_user, admin_session
):
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="Invalid date"):
            await get_fn(create)(title="bad", date="not-a-date")


@pytest.mark.asyncio
async def test_create_with_attachments(
    db_session, admin_user, admin_session, two_source_items
):
    with mcp_auth_context(admin_session.id):
        result = await get_fn(create)(
            title="Trip",
            date="2026-08-25",
            attachment_ids=[two_source_items[0].id, two_source_items[1].id],
        )

    assert sorted(result["attachment_ids"]) == sorted(
        [two_source_items[0].id, two_source_items[1].id]
    )
    # Happy path: nothing dropped, denied_attachments is empty.
    assert result["denied_attachments"] == []


@pytest.mark.asyncio
async def test_create_reports_denied_attachments(
    db_session,
    regular_user,
    user_session,
    regular_user_other_creator_item,
):
    """If the caller passes attachment IDs they can't access, the IDs are
    silently dropped from the deadline but surfaced in `denied_attachments`
    so the caller can react. Mirrors `attach`'s `denied` count."""
    # An item the regular_user owns (and thus can access).
    own_item = SourceItem(
        modality="text",
        sha256=create_content_hash("regular's own"),
        content="regular's own",
        sensitivity="basic",
        creator_id=regular_user.id,
    )
    db_session.add(own_item)
    db_session.commit()
    db_session.refresh(own_item)

    accessible_id = own_item.id
    inaccessible_id = regular_user_other_creator_item.id

    with mcp_auth_context(user_session.id):
        result = await get_fn(create)(
            title="mixed",
            date="2026-08-25",
            attachment_ids=[accessible_id, inaccessible_id],
        )

    # Only the accessible item is actually attached.
    assert result["attachment_ids"] == [accessible_id]
    # The inaccessible one shows up in denied_attachments.
    assert result["denied_attachments"] == [inaccessible_id]


@pytest.mark.asyncio
async def test_fetch_returns_deadline(
    db_session, admin_user, admin_session
):
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(title="t", date="2026-09-01")
        result = await get_fn(fetch)(deadline_id=created["id"])

    assert isinstance(result, dict)
    assert result["id"] == created["id"]
    assert result["title"] == "t"


@pytest.mark.asyncio
async def test_fetch_missing_returns_error(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        result = await get_fn(fetch)(deadline_id=99999)
    assert "error" in result


@pytest.mark.asyncio
async def test_update_changes_fields(db_session, admin_user, admin_session):
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(title="orig", date="2026-09-01")
        updated = await get_fn(update)(
            deadline_id=created["id"],
            title="renamed",
            priority="high",
            date="2026-09-05",
        )

    assert updated["title"] == "renamed"
    assert updated["priority"] == "high"
    assert updated["date"] == "2026-09-05"


@pytest.mark.asyncio
async def test_delete_removes_deadline(
    db_session, admin_user, admin_session
):
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(title="bye", date="2026-09-01")
        result = await get_fn(delete)(deadline_id=created["id"])

    assert result == {"success": True, "deadline_id": created["id"]}

    db_session.expire_all()
    assert db_session.get(Deadline, created["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_raises(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(delete)(deadline_id=99999)


@pytest.mark.asyncio
async def test_attach_idempotent(
    db_session, admin_user, admin_session, two_source_items
):
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(title="att", date="2026-09-01")
        first = await get_fn(attach)(
            deadline_id=created["id"],
            source_item_ids=[two_source_items[0].id],
        )
        second = await get_fn(attach)(
            deadline_id=created["id"],
            source_item_ids=[two_source_items[0].id, two_source_items[1].id],
        )

    assert first["attached"] == 1
    assert first["already_attached"] == 0
    assert first["denied"] == 0
    # Second call: one already attached, one newly attached.
    assert second["attached"] == 1
    assert second["already_attached"] == 1
    assert second["denied"] == 0


@pytest.mark.asyncio
async def test_detach_removes_links(
    db_session, admin_user, admin_session, two_source_items
):
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="d",
            date="2026-09-01",
            attachment_ids=[i.id for i in two_source_items],
        )
        result = await get_fn(detach)(
            deadline_id=created["id"],
            source_item_ids=[two_source_items[0].id],
        )
        fetched = await get_fn(fetch)(deadline_id=created["id"])

    assert result["detached"] == 1
    assert fetched["attachment_ids"] == [two_source_items[1].id]


@pytest.mark.asyncio
async def test_list_upcoming_orders_by_date_asc(
    db_session, admin_user, admin_session
):
    today = date.today()
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(title="far", date=(today + timedelta(days=10)).isoformat())
        await get_fn(create)(title="near", date=(today + timedelta(days=2)).isoformat())
        result = await get_fn(list_upcoming)(days=14)

    titles = [d["title"] for d in result]
    assert titles[: 2] == ["near", "far"]


@pytest.mark.asyncio
async def test_list_upcoming_excludes_past(
    db_session, admin_user, admin_session
):
    today = date.today()
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(title="past", date=(today - timedelta(days=3)).isoformat())
        await get_fn(create)(title="future", date=(today + timedelta(days=3)).isoformat())
        no_past = await get_fn(list_upcoming)(days=14)
        with_past = await get_fn(list_upcoming)(days=14, include_past=True)

    titles_no_past = [d["title"] for d in no_past]
    titles_with_past = [d["title"] for d in with_past]
    assert "past" not in titles_no_past
    assert "future" in titles_no_past
    assert "past" in titles_with_past


@pytest.mark.asyncio
async def test_list_upcoming_window_horizon(
    db_session, admin_user, admin_session
):
    today = date.today()
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(title="soon", date=(today + timedelta(days=3)).isoformat())
        await get_fn(create)(title="later", date=(today + timedelta(days=30)).isoformat())
        result = await get_fn(list_upcoming)(days=7)

    titles = [d["title"] for d in result]
    assert "soon" in titles
    assert "later" not in titles


@pytest.mark.asyncio
async def test_regular_user_cannot_see_admin_deadline(
    db_session, admin_session, user_session
):
    """Confidential deadline created by admin (no project) is invisible to regular user."""
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(
            title="admin-only",
            date=(date.today() + timedelta(days=2)).isoformat(),
            sensitivity="confidential",
        )

    with mcp_auth_context(user_session.id):
        result = await get_fn(list_upcoming)(days=14)

    assert all(d["title"] != "admin-only" for d in result)


@pytest.mark.asyncio
async def test_regular_user_can_see_own_deadline(
    db_session, regular_user, user_session
):
    """A regular user always sees deadlines they created (creator override)."""
    with mcp_auth_context(user_session.id):
        await get_fn(create)(
            title="mine",
            date=(date.today() + timedelta(days=2)).isoformat(),
            sensitivity="basic",
        )
        result = await get_fn(list_upcoming)(days=14)

    titles = [d["title"] for d in result]
    assert "mine" in titles


@pytest.mark.asyncio
async def test_public_deadline_not_broadcast_to_non_creators(
    db_session, admin_session, user_session
):
    """Deadlines run with `include_public=False` (notes-style: private
    artefacts). A `sensitivity="public"` deadline created by admin without a
    project is NOT visible to other users via list_upcoming, even though
    SourceItem's public-sensitivity would normally broadcast. Closes the
    foot-gun where a non-admin could globally expose a deadline by setting
    sensitivity=public + project_id=None.
    """
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(
            title="public-broadcast",
            date=(date.today() + timedelta(days=2)).isoformat(),
            sensitivity="public",
        )

    with mcp_auth_context(user_session.id):
        result = await get_fn(list_upcoming)(days=14)

    titles = [d["title"] for d in result]
    assert "public-broadcast" not in titles


@pytest.mark.asyncio
async def test_update_other_users_deadline_forbidden(
    db_session, admin_user, admin_session, regular_user, user_session
):
    """A regular user cannot update a deadline created by someone else.
    The error collapses not-found and not-permitted into the same ValueError
    so the LLM can't probe existence by reading the message text."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="admin-owned",
            date=(date.today() + timedelta(days=5)).isoformat(),
        )

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(update)(
                deadline_id=created["id"], title="hijacked"
            )


@pytest.mark.asyncio
async def test_delete_other_users_deadline_forbidden(
    db_session, admin_user, admin_session, regular_user, user_session
):
    """Existence-leak avoidance: a row the user can't edit raises the same
    not-found ValueError as a genuinely missing row. Matches the
    update/attach/detach convention; the message intentionally doesn't
    distinguish missing from forbidden."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="not-mine",
            date=(date.today() + timedelta(days=5)).isoformat(),
        )

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(delete)(deadline_id=created["id"])

    # Row should still exist; the regular user's call was a no-op.
    db_session.expire_all()
    assert db_session.get(Deadline, created["id"]) is not None


@pytest.mark.asyncio
async def test_attach_other_users_deadline_forbidden(
    db_session,
    admin_user,
    admin_session,
    regular_user,
    user_session,
    two_source_items,
):
    """A regular user cannot attach to a deadline created by someone else.
    The error collapses not-found and not-permitted into the same ValueError
    so the LLM can't probe existence by reading the message text. Mirrors
    the convention enforced on update / delete / detach."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="admin-owned-attach",
            date=(date.today() + timedelta(days=5)).isoformat(),
        )

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(attach)(
                deadline_id=created["id"],
                source_item_ids=[two_source_items[0].id],
            )


@pytest.mark.asyncio
async def test_detach_other_users_deadline_forbidden(
    db_session,
    admin_user,
    admin_session,
    regular_user,
    user_session,
    two_source_items,
):
    """A regular user cannot detach from a deadline created by someone else.
    Same not-found-or-forbidden collapse as the other mutators."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="admin-owned-detach",
            date=(date.today() + timedelta(days=5)).isoformat(),
            attachment_ids=[two_source_items[0].id],
        )

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(detach)(
                deadline_id=created["id"],
                source_item_ids=[two_source_items[0].id],
            )


def wire_user_into_team_project(db_session, user, team_role: str = "member"):
    """Wire user → Person → Team → Project with the given team role.

    Returns (user, project) so tests can create a deadline in the project
    and assert (or not) visibility based on the role/sensitivity ladder.
    """
    person = Person(
        identifier=f"person_{team_role}", display_name=f"Person {team_role}"
    )
    db_session.add(person)
    db_session.flush()

    user.person = person
    db_session.flush()

    project = Project(title=f"Shared Project ({team_role})", state="open")
    db_session.add(project)
    db_session.flush()

    team = Team(
        name=f"Shared Team ({team_role})",
        slug=f"shared-team-{team_role}",
        is_active=True,
    )
    db_session.add(team)
    db_session.flush()

    db_session.execute(
        team_members.insert().values(
            team_id=team.id, person_id=person.id, role=team_role
        )
    )
    db_session.execute(
        project_teams.insert().values(project_id=project.id, team_id=team.id)
    )
    db_session.commit()
    return user, project


@pytest.fixture
def user_with_project_access(db_session, regular_user):
    """Wire regular_user → Person → Team → Project (member role).

    Returns (user, project) so tests can create a deadline in the project
    and assert the regular user can see it.
    """
    return wire_user_into_team_project(db_session, regular_user, team_role="member")


@pytest.mark.asyncio
async def test_list_upcoming_visible_via_team_project_access(
    db_session,
    admin_session,
    user_session,
    user_with_project_access,
):
    """A non-admin who's a member of a Team assigned to a Project should see
    deadlines in that project. This was the BLOCKING bug — UserProxy lacks
    .person, so the project-roles lookup must go through user_id."""
    _, project = user_with_project_access

    with mcp_auth_context(admin_session.id):
        await get_fn(create)(
            title="team-visible",
            date=(date.today() + timedelta(days=3)).isoformat(),
            project_id=project.id,
            sensitivity="basic",
        )

    with mcp_auth_context(user_session.id):
        result = await get_fn(list_upcoming)(days=14)

    titles = [d["title"] for d in result]
    assert "team-visible" in titles


@pytest.mark.asyncio
async def test_list_upcoming_priority_orders_high_above_low(
    db_session, admin_user, admin_session
):
    """Same date — `high` priority should sort above `low`. Lexical sort on
    text would put `low` above `high`, which was the bug."""
    same_date = (date.today() + timedelta(days=4)).isoformat()
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(title="low-pri", date=same_date, priority="low")
        await get_fn(create)(title="high-pri", date=same_date, priority="high")
        result = await get_fn(list_upcoming)(days=14)

    same_date_results = [d for d in result if d["date"] == same_date]
    titles = [d["title"] for d in same_date_results]
    assert titles.index("high-pri") < titles.index("low-pri")


@pytest.mark.asyncio
async def test_update_clear_priority(db_session, admin_user, admin_session):
    """`clear=["priority"]` should reset the field to NULL — the regular
    `priority=None` arg can't distinguish "leave alone" from "clear"."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="x",
            date=(date.today() + timedelta(days=2)).isoformat(),
            priority="urgent",
        )
        assert created["priority"] == "urgent"

        cleared = await get_fn(update)(
            deadline_id=created["id"], clear=["priority"]
        )

    assert cleared["priority"] is None


@pytest.mark.asyncio
async def test_update_clear_unknown_field_raises(
    db_session, admin_user, admin_session
):
    """`clear=["title"]` is rejected — title isn't nullable and we don't want
    to encourage drive-by NULLing."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="x", date=(date.today() + timedelta(days=2)).isoformat()
        )
        with pytest.raises(ValueError, match="Cannot clear"):
            await get_fn(update)(
                deadline_id=created["id"], clear=["title"]
            )


@pytest.mark.asyncio
async def test_update_set_and_clear_same_field_raises(
    db_session, admin_user, admin_session
):
    """If a field is both passed explicitly and named in `clear`, the
    contradiction must be rejected loudly. Without the guard the explicit
    value is assigned, then the clear loop overwrites it to NULL — and the
    caller gets back a payload that silently discarded their input."""
    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="x", date=(date.today() + timedelta(days=2)).isoformat()
        )
        with pytest.raises(ValueError, match="Cannot both set and clear"):
            await get_fn(update)(
                deadline_id=created["id"],
                priority="high",
                clear=["priority"],
            )


@pytest.mark.asyncio
async def test_attach_drops_inaccessible_items(
    db_session,
    admin_session,
    regular_user,
    user_session,
    regular_user_other_creator_item,
):
    """The regular user can attach items they can see; items they can't see
    are silently dropped (counted as `denied`)."""
    # Public item is visible to regular_user; the fixture item is admin-only.
    visible_item = SourceItem(
        modality="text",
        sha256=create_content_hash("public-attach"),
        content="public-attach",
        sensitivity="public",
    )
    db_session.add(visible_item)
    db_session.commit()
    db_session.refresh(visible_item)

    visible = visible_item.id
    hidden = regular_user_other_creator_item.id

    with mcp_auth_context(user_session.id):
        deadline = await get_fn(create)(
            title="x",
            date=(date.today() + timedelta(days=2)).isoformat(),
        )
        # Pass `visible` twice: dedupe must kick in so we don't double-count
        # the duplicate as a denial. Also pass a non-existent ID — that
        # should count as one denial (not found is collapsed into denied).
        result = await get_fn(attach)(
            deadline_id=deadline["id"],
            source_item_ids=[visible, visible, hidden, 999_999_999],
        )
        fetched = await get_fn(fetch)(deadline_id=deadline["id"])

    assert visible in fetched["attachment_ids"]
    assert hidden not in fetched["attachment_ids"]
    assert result["attached"] == 1
    # 3 unique requested IDs, 1 accessible → 2 denied (the hidden one and
    # the non-existent ID). The duplicate visible ID must NOT add to denied.
    assert result["denied"] == 2


@pytest.mark.asyncio
async def test_create_no_project_confidential_allowed_for_regular_user(
    db_session, regular_user, user_session
):
    """Master's write helpers (require_project_membership +
    require_can_write_at_sensitivity) follow people.py: with `project_id=None`
    the row is creator-only, so the ladder isn't enforced — only the
    sensitivity string is validated. A non-admin pinning `confidential`
    without a project just gets a creator-only row that no other non-admin
    can see (list_upcoming runs with include_public=False, no project to
    bypass via).
    """
    with mcp_auth_context(user_session.id):
        result = await get_fn(create)(
            title="private-confidential",
            date=(date.today() + timedelta(days=2)).isoformat(),
            sensitivity="confidential",
        )
    assert result["sensitivity"] == "confidential"
    assert result["creator_id"] == regular_user.id


@pytest.mark.asyncio
async def test_create_no_project_basic_allowed_for_regular_user(
    db_session, regular_user, user_session
):
    """basic and public are still fine without a project."""
    with mcp_auth_context(user_session.id):
        result = await get_fn(create)(
            title="ok",
            date=(date.today() + timedelta(days=2)).isoformat(),
            sensitivity="basic",
        )
    assert result["sensitivity"] == "basic"


@pytest.mark.asyncio
async def test_create_no_project_confidential_allowed_for_admin(
    db_session, admin_session
):
    """Admins keep the carve-out — project_id=None + confidential is fine."""
    with mcp_auth_context(admin_session.id):
        result = await get_fn(create)(
            title="ok",
            date=(date.today() + timedelta(days=2)).isoformat(),
            sensitivity="confidential",
        )
    assert result["sensitivity"] == "confidential"


# Role × sensitivity grid for team-project visibility. The deadlines code
# delegates to build_access_filter / ROLE_SENSITIVITY, so this is mostly
# regression insurance against future refactors of the deadlines path.
# Mapping: team-role -> project-role:
#   member  -> contributor (sees public, basic)
#   lead    -> manager     (sees public, basic, internal)
#   admin   -> admin       (sees public, basic, internal, confidential)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "team_role, deadline_sensitivity, should_see",
    [
        ("member", "public", True),
        ("member", "basic", True),
        ("member", "internal", False),
        ("member", "confidential", False),
        ("lead", "basic", True),
        ("lead", "internal", True),
        ("lead", "confidential", False),
        ("admin", "internal", True),
        ("admin", "confidential", True),
    ],
)
async def test_list_upcoming_role_sensitivity_grid(
    db_session,
    admin_session,
    regular_user,
    user_session,
    team_role,
    deadline_sensitivity,
    should_see,
):
    """Verify the role × sensitivity ladder for team-project visibility on
    deadlines: contributors only see public/basic, managers also see
    internal, admins see everything."""
    _, project = wire_user_into_team_project(
        db_session, regular_user, team_role=team_role
    )

    title = f"d-{team_role}-{deadline_sensitivity}"
    with mcp_auth_context(admin_session.id):
        await get_fn(create)(
            title=title,
            date=(date.today() + timedelta(days=3)).isoformat(),
            project_id=project.id,
            sensitivity=deadline_sensitivity,
        )

    with mcp_auth_context(user_session.id):
        result = await get_fn(list_upcoming)(days=14)

    titles = [d["title"] for d in result]
    assert (title in titles) is should_see


@pytest.mark.asyncio
async def test_update_clear_project_id_allowed_for_non_admin_at_high_sensitivity(
    db_session, regular_user, user_session
):
    """Master's write helpers don't enforce the ladder when `project_id`
    becomes None — the row is creator-only after the clear, and
    `list_upcoming(include_public=False)` plus the project invariant
    (NULL → admin-only for non-creators) prevents other users from seeing
    it. So a non-admin lead can clear project_id from an internal-sensitivity
    deadline they own and end up with a private-to-them confidential-shaped
    row. This matches people.py's semantics.
    """
    _, lead_project = wire_user_into_team_project(
        db_session, regular_user, team_role="lead"
    )

    with mcp_auth_context(user_session.id):
        created = await get_fn(create)(
            title="hidden-in-project",
            date=(date.today() + timedelta(days=4)).isoformat(),
            project_id=lead_project.id,
            sensitivity="internal",
        )

        cleared = await get_fn(update)(
            deadline_id=created["id"], clear=["project_id"]
        )

    assert cleared["project_id"] is None
    assert cleared["sensitivity"] == "internal"


@pytest.mark.asyncio
async def test_update_clear_project_id_allowed_for_non_admin_at_basic(
    db_session, regular_user, user_session, user_with_project_access
):
    """The ladder block only applies to internal/confidential — basic and
    public can still drop their project_id."""
    _, project = user_with_project_access

    with mcp_auth_context(user_session.id):
        created = await get_fn(create)(
            title="reassignable",
            date=(date.today() + timedelta(days=4)).isoformat(),
            project_id=project.id,
            sensitivity="basic",
        )
        cleared = await get_fn(update)(
            deadline_id=created["id"], clear=["project_id"]
        )

    assert cleared["project_id"] is None


@pytest.mark.asyncio
async def test_update_clear_project_id_allowed_for_admin_at_high_sensitivity(
    db_session, admin_session
):
    """Admins keep the carve-out: they can park confidential rows with no
    project (mirrors `test_create_no_project_confidential_allowed_for_admin`).

    Exercises the *asymmetric* set-then-clear path: create with a real
    project, then `clear=["project_id"]` while sensitivity is confidential.
    Non-admins are blocked on that exact path; admins are not.
    """
    project = Project(title="Admin Park Project", state="open")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    with mcp_auth_context(admin_session.id):
        created = await get_fn(create)(
            title="admin-park",
            date=(date.today() + timedelta(days=4)).isoformat(),
            project_id=project.id,
            sensitivity="confidential",
        )
        # Set then unset: the asymmetric path the block protects against for
        # non-admins. Admins keep the carve-out.
        cleared = await get_fn(update)(
            deadline_id=created["id"], clear=["project_id"]
        )

    assert cleared["project_id"] is None


@pytest.mark.asyncio
async def test_update_move_to_foreign_project_forbidden_for_non_admin(
    db_session, regular_user, user_session, user_with_project_access
):
    """Non-admin contributor in project A creates a basic deadline there, then
    tries to move it to project B where they have no role. The post-update
    ladder must reject the move (covers BLOCKING exploit path 1)."""
    _, project_a = user_with_project_access

    # Project B is created without giving regular_user any role on it.
    project_b = Project(title="Foreign Project", state="open")
    db_session.add(project_b)
    db_session.commit()
    db_session.refresh(project_b)
    foreign_project_id = project_b.id

    with mcp_auth_context(user_session.id):
        created = await get_fn(create)(
            title="moveable",
            date=(date.today() + timedelta(days=3)).isoformat(),
            project_id=project_a.id,
            sensitivity="basic",
        )

        with pytest.raises(PermissionError, match="(?:not a member|does not permit)"):
            await get_fn(update)(
                deadline_id=created["id"], project_id=foreign_project_id
            )


@pytest.mark.asyncio
async def test_update_elevate_sensitivity_in_project_forbidden_for_non_admin(
    db_session, regular_user, user_session, user_with_project_access
):
    """Non-admin contributor (member role → contributor) creates a basic
    deadline in their project, then tries to elevate sensitivity to
    confidential. Contributors can't author confidential, so the post-update
    ladder must reject the elevation (covers BLOCKING exploit path 2)."""
    _, project = user_with_project_access

    with mcp_auth_context(user_session.id):
        created = await get_fn(create)(
            title="elevate-me",
            date=(date.today() + timedelta(days=3)).isoformat(),
            project_id=project.id,
            sensitivity="basic",
        )

        with pytest.raises(PermissionError, match="(?:not a member|does not permit)"):
            await get_fn(update)(
                deadline_id=created["id"], sensitivity="confidential"
            )


@pytest.mark.asyncio
async def test_update_elevate_sensitivity_on_null_project_allowed_for_non_admin(
    db_session, regular_user, user_session
):
    """Mirrors create-time semantics: with `project_id=None` the row is
    creator-only and master's write helpers don't enforce the role-vs-
    sensitivity ladder. Non-admins can therefore elevate sensitivity on their
    own project-less deadlines without raising. The row stays invisible to
    other non-admins via list_upcoming(include_public=False) plus the
    NULL-project invariant.
    """
    with mcp_auth_context(user_session.id):
        created = await get_fn(create)(
            title="park-and-elevate",
            date=(date.today() + timedelta(days=3)).isoformat(),
            sensitivity="basic",
        )
        elevated = await get_fn(update)(
            deadline_id=created["id"], sensitivity="internal"
        )

    assert elevated["sensitivity"] == "internal"
    assert elevated["project_id"] is None


@pytest.mark.asyncio
async def test_update_no_op_after_demotion_allowed(
    db_session, regular_user, user_session
):
    """A user who was a `lead` in project P, created an `internal` deadline
    there (legitimately), then got demoted to `member` should still be able
    to fix typos in their own deadline. The post-update ladder only re-runs
    when (project_id, sensitivity) actually changes — title-only edits are
    not gated by current role.

    Without this guard, role demotion silently locks the user out of editing
    rows they own and authored under the prior role, even for AC-irrelevant
    fields. With it, only AC-relevant mutations (move project / change
    sensitivity) re-check the ladder.
    """
    user, project = wire_user_into_team_project(
        db_session, regular_user, team_role="lead"
    )

    with mcp_auth_context(user_session.id):
        created = await get_fn(create)(
            title="lead-authored",
            date=(date.today() + timedelta(days=3)).isoformat(),
            project_id=project.id,
            sensitivity="internal",
        )

    # Simulate demotion: drop the user's team_members row entirely.
    # `member` would be enough to cause the failure too (contributors can't
    # author internal); we drop the membership outright to make the
    # demotion stark.
    db_session.execute(
        team_members.delete().where(
            team_members.c.person_id == user.person.id
        )
    )
    db_session.commit()

    # Title-only update: AC tuple unchanged. Should succeed.
    with mcp_auth_context(user_session.id):
        updated = await get_fn(update)(
            deadline_id=created["id"], title="typo-fixed"
        )

    assert updated["title"] == "typo-fixed"
    # AC tuple is preserved; nothing was silently downgraded.
    assert updated["project_id"] == project.id
    assert updated["sensitivity"] == "internal"

    # Sanity: trying to actually mutate the AC tuple should still be
    # rejected — the guard didn't disable the ladder, just made it
    # conditional.
    with mcp_auth_context(user_session.id):
        with pytest.raises(PermissionError, match="(?:not a member|does not permit)"):
            await get_fn(update)(
                deadline_id=created["id"], sensitivity="confidential"
            )
