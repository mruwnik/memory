"""MCP subserver for deadlines — first-class date-anchored bundles.

Tools:
- list_upcoming: surface deadlines in the next N days, AC-filtered
- fetch: get one deadline with its attached SourceItems
- create / update / delete: manage deadlines
- attach / detach: link or unlink SourceItems to a deadline
"""

from __future__ import annotations

import logging
from datetime import date as date_type, timedelta
from typing import Literal, Sequence

from fastmcp import FastMCP
from sqlalchemy import case
from sqlalchemy.orm import selectinload

from memory.api.MCP.access import (
    ALLOWED_SENSITIVITIES,
    get_mcp_current_user,
    get_project_roles_by_user_id,
    require_can_write_at_sensitivity,
    require_project_membership,
)
from memory.api.MCP.visibility import has_items, require_scopes, visible_when
from memory.common.access_control import (
    apply_access_filter_to_query,
    build_access_filter,
    has_admin_scope,
    user_can_access,
    user_can_edit,
)
from memory.common.db.connection import make_session
from memory.common.db.models import Deadline, DeadlinePayload, SourceItem
from memory.common.scopes import (
    SCOPE_ORGANIZER,
    SCOPE_ORGANIZER_WRITE,
    SensitivityLevelLiteral,
    TaskPriorityLiteral,
)

logger = logging.getLogger(__name__)

deadlines_mcp = FastMCP("memory-deadlines")


def parse_date(value: str, field: str) -> date_type:
    """Parse a YYYY-MM-DD string. Re-raises with a clearer message than
    ``date.fromisoformat``'s default. ``parse_iso_datetime`` in
    ``memory.common.dates`` is the existing helper for full datetimes; for
    a plain date column we just need ``date.fromisoformat`` with a friendlier
    error.
    """
    try:
        return date_type.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"Invalid {field}: expected YYYY-MM-DD, got {value!r}") from e


def require_user():
    user = get_mcp_current_user()
    if user is None or user.id is None:
        raise ValueError("Authentication required")
    return user


def enforce_write_ladder(user, project_id: int | None, sensitivity: str) -> None:
    """Enforce master's project + sensitivity write checks.

    Mirrors the pattern used in ``MCP/servers/people.py`` (and other
    deep-audit-era write tools): if a project is named, validate membership
    AND the role-vs-sensitivity matrix; otherwise just validate the
    sensitivity string. ``require_can_write_at_sensitivity`` short-circuits
    on admin scope, so admins skip both branches without us reaching for
    ``has_admin_scope`` here.
    """
    if project_id is not None:
        require_project_membership(user, project_id)
        require_can_write_at_sensitivity(user, project_id, sensitivity)
    elif sensitivity not in ALLOWED_SENSITIVITIES:
        raise ValueError(
            f"Invalid sensitivity {sensitivity!r}; must be one of "
            f"{sorted(ALLOWED_SENSITIVITIES)}."
        )


# Lexical sort on text would give "urgent > medium > low > high"; rank
# numerically so high-priority items surface first within a date.
PRIORITY_RANK = case(
    (Deadline.priority == "urgent", 4),
    (Deadline.priority == "high", 3),
    (Deadline.priority == "medium", 2),
    (Deadline.priority == "low", 1),
    else_=0,
)

CLEARABLE_FIELDS = frozenset({"priority", "owner_id", "description", "project_id"})


@deadlines_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER), has_items(Deadline))
async def list_upcoming(
    days: int = 14,
    include_past: bool = False,
    priority: TaskPriorityLiteral | None = None,
    project_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DeadlinePayload]:
    """
    List upcoming deadlines within a time window.
    Use to see what's coming up, find what to prep for, or surface anything
    about to go past.

    Args:
        days: Look this many days ahead from today (default 14, max 365).
        include_past: Include deadlines whose date has passed (default False).
        priority: Filter to only this priority level.
        project_id: Filter to only this project.
        limit: Maximum results (default 50, max 500).
        offset: Number of results to skip for pagination (default 0, max 10000).

    Returns: list of deadlines, ordered by date ascending.
    """
    days = min(max(days, 1), 365)
    limit = min(max(limit, 1), 500)
    offset = min(max(offset, 0), 10000)

    today = date_type.today()
    horizon = today + timedelta(days=days)

    user = require_user()

    with make_session() as session:
        project_roles: dict[int, str] = (
            {} if has_admin_scope(user) else get_project_roles_by_user_id(user.id, session)
        )

        query = session.query(Deadline).options(selectinload(Deadline.attachments))
        if not include_past:
            query = query.filter(Deadline.date >= today)
        query = query.filter(Deadline.date <= horizon)
        if priority is not None:
            query = query.filter(Deadline.priority == priority)
        if project_id is not None:
            query = query.filter(Deadline.project_id == project_id)

        # include_public=False: deadlines are personal/team artefacts.
        # Without this, a non-admin marking sensitivity="public" with no
        # project_id would broadcast to every authenticated user — same
        # rationale notes.py:note_files uses (see "private artefacts" note
        # there).
        access_filter = build_access_filter(
            user, project_roles, include_public=False
        )
        query = apply_access_filter_to_query(query, access_filter, model=Deadline)

        query = query.order_by(Deadline.date.asc(), PRIORITY_RANK.desc())
        deadlines = query.offset(offset).limit(limit).all()
        return [d.as_payload() for d in deadlines]


@deadlines_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER), has_items(Deadline))
async def fetch(deadline_id: int) -> DeadlinePayload | dict:
    """
    Get a single deadline by ID, including its attached SourceItem IDs.

    Args:
        deadline_id: The deadline ID.

    Returns: the deadline payload, or {"error": "..."} if not found / no access.
    """
    user = require_user()
    with make_session() as session:
        # No selectinload(Deadline.attachments) here: this is a single-row
        # endpoint, so as_payload() reading self.attachments is at most one
        # extra SELECT — there's no N+1 to amortise. list_upcoming uses
        # selectinload because it iterates many rows.
        deadline = session.get(Deadline, deadline_id)
        if deadline is None:
            return {"error": f"Deadline {deadline_id} not found"}

        if not has_admin_scope(user):
            project_roles = get_project_roles_by_user_id(user.id, session)
            if not user_can_access(user, deadline, project_roles):
                return {"error": f"Deadline {deadline_id} not found"}

        return deadline.as_payload()


@deadlines_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER_WRITE))
async def upsert(
    deadline_id: int | None = None,
    title: str | None = None,
    date: str | None = None,
    priority: TaskPriorityLiteral | None = None,
    owner_id: int | None = None,
    description: str | None = None,
    project_id: int | None = None,
    sensitivity: SensitivityLevelLiteral | None = None,
    tags: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    clear: list[
        Literal["priority", "owner_id", "description", "project_id"]
    ]
    | None = None,
) -> dict:
    """
    Create or update a deadline (a first-class date-anchored bundle).

    Use when the user wants to track a date they need to be ready for —
    a grant deadline, a trip, an application due date — and may want to
    attach related context (tickets, drafts, references) to it.

    Mode is selected by `deadline_id`:
    - `deadline_id=None` (or omitted): **create**. `title` and `date` are
      required. `sensitivity` defaults to `basic`. `attachment_ids` is the
      initial set of attached SourceItems.
    - `deadline_id=<int>`: **update**. Only fields you pass are changed; the
      rest are left alone. `attachment_ids` is rejected on update — use
      `attach` / `detach` for managing attachments after creation.

    Args:
        deadline_id: ID of an existing deadline to update, or None to create.
        title: Required on create. On update, ignored if None.
        date: Required on create. ISO format (YYYY-MM-DD). On update, ignored
            if None.
        priority: low | medium | high | urgent.
        owner_id: ID of the Person responsible (people.id).
        description: Free-text notes.
        project_id: Project for access control. Defaults to None on create.
        sensitivity: public | basic | internal | confidential. Defaults basic
            on create. With a `project_id` set, the role-vs-sensitivity matrix
            in ``ROLE_SENSITIVITY`` applies (contributor → public/basic;
            manager → +internal; admin → +confidential). With
            `project_id=None` the row is creator-only regardless of
            sensitivity, so the matrix is not enforced — but
            ``list_upcoming`` runs with ``include_public=False`` so a
            non-admin can't broadcast a project-less public deadline.
            On update, the same ladder runs against the *effective*
            post-update tuple, but only when `(project_id, sensitivity)`
            actually changes — so a user whose role has since been demoted
            can still edit unrelated fields on deadlines they own.
        tags: List of tags for categorization.
        attachment_ids: Create-only. SourceItem IDs to attach immediately.
            Items the caller can't access are silently dropped and surfaced
            in the returned `denied_attachments` field.
        clear: Update-only. Field names to set to NULL. Useful because the
            regular params can't distinguish "leave alone" from "clear" once
            a value has been written. Allowed: priority, owner_id,
            description, project_id. A field passed both as `clear` and as
            an explicit non-None value raises (the contradiction would
            otherwise resolve silently to NULL).

    Returns: the deadline payload. On create, includes a `denied_attachments`
        field listing any attachment IDs that were filtered for access (or
        don't exist — we don't distinguish, to avoid leaking existence).

    Raises:
        ValueError on bad date format, unknown clear field, contradictory
            set+clear, missing title/date on create, attachment_ids on
            update, or update of a non-existent / not-editable deadline (the
            last two collapse into the same "not found" message to avoid
            leaking existence).
        PermissionError when the (effective) `(project_id, sensitivity)`
            tuple violates the role-vs-sensitivity ladder for the caller.
    """
    user = require_user()
    is_create = deadline_id is None

    # Validate `clear` shape up front, regardless of mode.
    clear = clear or []
    unknown = [f for f in clear if f not in CLEARABLE_FIELDS]
    if unknown:
        raise ValueError(
            f"Cannot clear unknown field(s): {unknown}. "
            f"Allowed: {sorted(CLEARABLE_FIELDS)}"
        )
    explicit_values = {
        "priority": priority,
        "owner_id": owner_id,
        "description": description,
        "project_id": project_id,
    }
    contradiction = sorted(
        f for f in clear if explicit_values.get(f) is not None
    )
    if contradiction:
        raise ValueError(
            f"Cannot both set and clear field(s): {contradiction}"
        )

    if is_create:
        if title is None or date is None:
            raise ValueError("title and date are required when creating a deadline")
        if clear:
            # Clearing fields on create is meaningless — every nullable column
            # starts NULL. Reject so callers learn quickly rather than getting
            # an unexpected NULL on a field they thought they'd just set.
            raise ValueError("clear is not supported when creating a deadline")
        return _create_deadline(
            user=user,
            title=title,
            date=date,
            priority=priority,
            owner_id=owner_id,
            description=description,
            project_id=project_id,
            sensitivity=sensitivity or "basic",
            tags=tags,
            attachment_ids=attachment_ids,
        )

    if attachment_ids is not None:
        raise ValueError(
            "attachment_ids cannot be set on update — use attach/detach"
        )
    return _update_deadline(
        user=user,
        deadline_id=deadline_id,
        title=title,
        date=date,
        priority=priority,
        owner_id=owner_id,
        description=description,
        project_id=project_id,
        sensitivity=sensitivity,
        tags=tags,
        clear=clear,
    )


def _create_deadline(
    *,
    user,
    title: str,
    date: str,
    priority: str | None,
    owner_id: int | None,
    description: str | None,
    project_id: int | None,
    sensitivity: str,
    tags: list[str] | None,
    attachment_ids: list[int] | None,
) -> dict:
    parsed_date = parse_date(date, "date")
    enforce_write_ladder(user, project_id, sensitivity)

    with make_session() as session:
        deadline = Deadline(
            title=title,
            date=parsed_date,
            description=description,
            priority=priority,
            owner_id=owner_id,
            project_id=project_id,
            sensitivity=sensitivity,
            creator_id=user.id,
            tags=tags or [],
        )

        denied_attachments: list[int] = []
        if attachment_ids:
            attachments = accessible_source_items(session, user, attachment_ids)
            deadline.attachments = attachments
            attached_ids = {a.id for a in attachments}
            # Dedupe inputs and surface dropped IDs so the caller knows
            # which were silently filtered (no access / not found).
            denied_attachments = sorted(set(attachment_ids) - attached_ids)

        session.add(deadline)
        session.flush()
        session.refresh(deadline)
        return {**deadline.as_payload(), "denied_attachments": denied_attachments}


def _update_deadline(
    *,
    user,
    deadline_id: int,
    title: str | None,
    date: str | None,
    priority: str | None,
    owner_id: int | None,
    description: str | None,
    project_id: int | None,
    sensitivity: str | None,
    tags: list[str] | None,
    clear: Sequence[str],
) -> dict:
    with make_session() as session:
        deadline = session.get(Deadline, deadline_id)
        # Collapse not-found and not-permitted into the same error shape so
        # the LLM can't probe existence by reading the message text.
        if deadline is None or not user_can_edit(user, deadline):
            raise ValueError(f"Deadline {deadline_id} not found")

        # Capture the pre-mutation AC-relevant tuple so we only re-run the
        # create-time ladder when it actually changed. Without this guard a
        # user demoted out of a role since creation can't even fix typos in
        # their own deadline.
        original_project_id = deadline.project_id
        original_sensitivity = deadline.sensitivity

        if title is not None:
            deadline.title = title
        if date is not None:
            deadline.date = parse_date(date, "date")
        if priority is not None:
            deadline.priority = priority
        if owner_id is not None:
            deadline.owner_id = owner_id
        if description is not None:
            deadline.description = description
        if project_id is not None:
            deadline.project_id = project_id
        if sensitivity is not None:
            deadline.sensitivity = sensitivity
        if tags is not None:
            deadline.tags = tags

        for field in clear:
            setattr(deadline, field, None)

        # Replay the create-time ladder against the *effective* post-update
        # tuple — but only if (project_id, sensitivity) actually changed.
        # Catches the three exploit paths (move into a foreign project,
        # elevate sensitivity in-project, elevate sensitivity on a
        # NULL-project row) without locking demoted users out of edits to
        # unrelated fields on deadlines they already own.
        ac_tuple_changed = (
            (deadline.project_id, deadline.sensitivity)
            != (original_project_id, original_sensitivity)
        )
        if ac_tuple_changed:
            enforce_write_ladder(
                user, deadline.project_id, deadline.sensitivity
            )

        session.flush()
        session.refresh(deadline)
        return dict(deadline.as_payload())


@deadlines_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER_WRITE), has_items(Deadline))
async def delete(deadline_id: int) -> dict:
    """
    Delete a deadline. Attached SourceItems are NOT deleted — only the
    junction rows are removed.

    Args:
        deadline_id: ID of the deadline.

    Returns: {"success": True, "deadline_id": <id>} on delete.

    Raises ValueError if the deadline does not exist or the caller cannot
    edit it (the two cases are deliberately indistinguishable to avoid
    leaking existence — same convention as update / attach / detach).
    """
    user = require_user()
    with make_session() as session:
        deadline = session.get(Deadline, deadline_id)
        if deadline is None or not user_can_edit(user, deadline):
            raise ValueError(f"Deadline {deadline_id} not found")
        session.delete(deadline)
        return {"success": True, "deadline_id": deadline_id}


@deadlines_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER_WRITE), has_items(Deadline))
async def attach(deadline_id: int, source_item_ids: list[int]) -> dict:
    """
    Attach SourceItems to a deadline. Idempotent — already-attached items
    are ignored. Only items the user can access are attached.

    Args:
        deadline_id: ID of the deadline.
        source_item_ids: SourceItem IDs to attach.

    Returns: {"attached": <int>, "already_attached": <int>, "denied": <int>}.
        "denied" covers both "user can't see this item" and "no such item" —
        we don't distinguish them so we don't leak existence.
        Counts (attached / already_attached / denied) are over *unique* IDs —
        duplicates in source_item_ids are deduplicated before counting, so
        the sum may be less than `len(source_item_ids)`.
    """
    user = require_user()
    with make_session() as session:
        deadline = session.get(Deadline, deadline_id)
        # Collapse not-found and not-permitted into the same error shape so
        # the LLM can't probe existence by reading the message text.
        if deadline is None or not user_can_edit(user, deadline):
            raise ValueError(f"Deadline {deadline_id} not found")

        accessible = accessible_source_items(session, user, source_item_ids)
        existing_ids = {a.id for a in deadline.attachments}

        added = 0
        already_attached = 0
        for item in accessible:
            if item.id in existing_ids:
                already_attached += 1
            else:
                deadline.attachments.append(item)
                added += 1

        # Dedupe input first: callers might pass [5, 5, 5] for an accessible
        # item 5 and that should report denied=0, not denied=2. We also lump
        # "not found" into "denied" deliberately so we don't leak existence —
        # the docstring calls this out.
        requested_unique = set(source_item_ids)
        accessible_ids = {a.id for a in accessible}
        denied = len(requested_unique - accessible_ids)
        return {
            "attached": added,
            "already_attached": already_attached,
            "denied": denied,
        }


@deadlines_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER_WRITE), has_items(Deadline))
async def detach(deadline_id: int, source_item_ids: list[int]) -> dict:
    """
    Detach SourceItems from a deadline. SourceItems themselves are not
    deleted — only the junction rows.

    Args:
        deadline_id: ID of the deadline.
        source_item_ids: SourceItem IDs to detach.

    Returns: {"detached": <int>}.
    """
    user = require_user()
    with make_session() as session:
        deadline = session.get(Deadline, deadline_id)
        # Collapse not-found and not-permitted into the same error shape so
        # the LLM can't probe existence by reading the message text.
        if deadline is None or not user_can_edit(user, deadline):
            raise ValueError(f"Deadline {deadline_id} not found")

        target_ids = set(source_item_ids)
        keep = [a for a in deadline.attachments if a.id not in target_ids]
        removed = len(deadline.attachments) - len(keep)
        deadline.attachments = keep
        return {"detached": removed}


def accessible_source_items(
    session, user, source_item_ids: list[int]
) -> list[SourceItem]:
    """Load SourceItems by ID, filtered to those the user can access."""
    if not source_item_ids:
        return []

    items = session.query(SourceItem).filter(SourceItem.id.in_(source_item_ids)).all()

    if has_admin_scope(user):
        return items

    project_roles = get_project_roles_by_user_id(user.id, session)
    return [item for item in items if user_can_access(user, item, project_roles)]
