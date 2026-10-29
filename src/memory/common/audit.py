"""Audit records: who did what, readable by admins only."""

from typing import Any, Literal, get_args

from memory.common.db.connection import make_session
from memory.common.db.models import AuditEvent, User

AuditAction = Literal["mcp.call", "settings.update", "settings.restart"]
# The snapshot action is no longer written, but its old rows are in audit_events.
AUDIT_ACTIONS: frozenset[str] = frozenset(get_args(AuditAction)) | {
    "team.external_membership_snapshot"
}


def record_audit(
    action: AuditAction,
    *,
    actor_id: int,
    target: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Write one audit row in its own committed transaction, so it persists
    even if the caller's transaction rolls back or the audited call fails."""
    with make_session() as session:
        actor = session.get(User, actor_id)
        session.add(
            AuditEvent(
                actor_id=actor.id if actor else None,
                actor_email=actor.email if actor else None,
                action=action,
                target=target,
                attributes=attributes or {},
            )
        )
        session.commit()
