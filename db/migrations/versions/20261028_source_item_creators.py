"""Backfill source_item.creator_id from the owners of the sources items were synced from.

Revision ID: 20261028_source_item_creators
Revises: 20261027_llm_provider_keys
Create Date: 2026-10-28

Only creator_id is written here. Meetings, their action-item tasks and email
attachments also start inheriting project_id / sensitivity, and Qdrant
payloads gain creator_id; reconcile_access_control writes both (see
CHANGELOG).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_source_item_creators"
down_revision: Union[str, None] = "20261027_llm_provider_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH_SIZE = 50_000

# Each statement fills creator_id for one source type, for source_item ids in
# [:lo, :hi). The range is repeated on the subtype alias so Postgres can use
# its primary key too. Parents come before what is derived from them
# (messages before attachments, meetings before tasks), since derived items
# copy the parent's creator. Slack messages get none: an app's registrar must
# not own DMs collected through other users' authorizations.
BACKFILLS = [
    """
    UPDATE source_item s SET creator_id = a.user_id
    FROM mail_message m JOIN email_accounts a ON a.id = m.email_account_id
    WHERE m.id = s.id AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND m.id >= :lo AND m.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = parent.creator_id
    FROM email_attachment att JOIN source_item parent ON parent.id = att.mail_message_id
    WHERE att.id = s.id AND parent.creator_id IS NOT NULL AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND att.id >= :lo AND att.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = a.user_id
    FROM github_item g
    JOIN github_repos r ON r.id = g.repo_id
    JOIN github_accounts a ON a.id = r.account_id
    WHERE g.id = s.id AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND g.id >= :lo AND g.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = a.user_id
    FROM google_doc d
    JOIN google_folders f ON f.id = d.folder_id
    JOIN google_accounts a ON a.id = f.account_id
    WHERE d.id = s.id AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND d.id >= :lo AND d.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = a.user_id
    FROM calendar_event e JOIN calendar_accounts a ON a.id = e.calendar_account_id
    WHERE e.id = s.id AND a.user_id IS NOT NULL AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND e.id >= :lo AND e.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = f.user_id
    FROM blog_post b JOIN article_feeds f ON f.id = b.feed_id
    WHERE b.id = s.id AND f.user_id IS NOT NULL AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND b.id >= :lo AND b.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = a.user_id
    FROM meeting mt JOIN transcript_accounts a ON a.id = mt.transcript_account_id
    WHERE mt.id = s.id AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND mt.id >= :lo AND mt.id < :hi
    """,
    """
    UPDATE source_item s SET creator_id = parent.creator_id
    FROM task t JOIN source_item parent ON parent.id = t.source_item_id
    WHERE t.id = s.id AND parent.creator_id IS NOT NULL AND s.creator_id IS NULL
      AND s.id >= :lo AND s.id < :hi AND t.id >= :lo AND t.id < :hi
    """,
]


def backfill(bind, batch_size: int = BATCH_SIZE) -> None:
    lo, hi = bind.execute(sa.text("SELECT MIN(id), MAX(id) FROM source_item")).one()
    if lo is None:
        return
    for statement in BACKFILLS:
        for start in range(lo, hi + 1, batch_size):
            bind.execute(sa.text(statement), {"lo": start, "hi": start + batch_size})


def upgrade() -> None:
    # Commit per batch so a large source_item table isn't locked row-by-row
    # in one long transaction. Every statement skips rows that already have a
    # creator, so a re-run after a failure picks up where it stopped.
    with op.get_context().autocommit_block():
        backfill(op.get_bind())


def downgrade() -> None:
    # Backfilled creators can't be told apart from ones set at ingest, so they stay.
    pass
