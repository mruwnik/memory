"""Push channels that stopped delivering: a poll stores something push should
have delivered already. Worker-safe: no API imports."""

from datetime import datetime, timedelta, timezone

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import User
from memory.common.db.models.slack import SlackApp
from memory.common.db.models.sources import GithubRepo
from memory.common.notify import notify_user_now

# How long a change may wait for its push (Slack retries, GitHub's
# redeliveries and our refetch debounce) before a poll counts it as missed.
PUSH_GRACE = timedelta(minutes=10)
# The webhook_status values of a repo whose events reach us.
GITHUB_PUSH_STATUSES = ("installed", "org_hook")


def push_stale(last_event_at: datetime | None) -> bool:
    """No event within PUSH_STALE_HOURS: a quiet source, or a push channel that broke."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.PUSH_STALE_HOURS)
    return last_event_at is None or last_event_at < cutoff


def push_overdue(at: datetime | None, push_since: datetime | None) -> bool:
    """Whether push, delivering since ``push_since``, should have delivered a change made ``at``."""
    if at is None or push_since is None:
        return False
    return push_since < at < datetime.now(timezone.utc) - PUSH_GRACE


def record_push_miss(row: type[GithubRepo] | type[SlackApp], row_id: int, owner_id: int | None, label: str) -> None:
    """Stamp a miss; the one that starts an outage tells the owner."""
    now = datetime.now(timezone.utc)
    with make_session() as session:
        session.query(row).filter(row.id == row_id).update(
            {row.push_last_miss_at: now}, synchronize_session=False
        )
        # Conditional on NULL so concurrent misses start one outage, not several.
        started = (
            session.query(row)
            .filter(row.id == row_id, row.push_missing_since.is_(None))
            .update({row.push_missing_since: now}, synchronize_session=False)
        )
        session.commit()
        user = session.get(User, owner_id) if started and owner_id is not None else None
        if user is None:
            return
        notify_user_now(
            session,
            user,
            f"Push events stopped arriving for {label}",
            "Polling found changes that push never delivered, so they arrive late. "
            "The webhook may have been deleted or disabled, its secret rotated outside "
            "Memory, a proxy may block the webhook URL, or the app's event subscriptions "
            "may be off.",
            f"{settings.SERVER_URL.rstrip('/')}/sources",
        )
