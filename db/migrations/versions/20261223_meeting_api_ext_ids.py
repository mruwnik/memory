"""Namespace meeting external ids submitted through POST /meetings to their submitter.

POST /meetings now stores a submitted ``external_id`` as ``api:<user id>:<id>``.
Rename the meetings already stored that way (no transcript account, not a
transcript push, with a creator, not already in that creator's namespace)
so resubmitting their ids still dedupes.
Meetings whose transcript account was deleted look the same and are renamed
too; a re-added account's sync then finds them as another recording of the
same call rather than by id.

Revision ID: 20261223_meeting_api_ext_ids
Revises: 20261222_item_inserted_idx
Create Date: 2026-11-10
"""

from alembic import op

revision = "20261223_meeting_api_ext_ids"
down_revision = "20261222_item_inserted_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE meeting AS m
        SET external_id = 'api:' || s.creator_id || ':' || m.external_id
        FROM source_item AS s
        WHERE s.id = m.id
          AND m.external_id IS NOT NULL
          AND m.transcript_account_id IS NULL
          AND m.external_id NOT LIKE 'push:%'
          AND s.creator_id IS NOT NULL
          AND m.external_id NOT LIKE 'api:' || s.creator_id || ':%'
        """
    )


def downgrade() -> None:
    # Two users may now hold the same id; the later one keeps its prefix.
    op.execute(
        """
        UPDATE meeting AS m
        SET external_id = regexp_replace(m.external_id, '^api:[0-9]+:', '')
        WHERE m.external_id ~ '^api:[0-9]+:'
          AND m.id = (
            SELECT min(o.id) FROM meeting AS o
            WHERE regexp_replace(o.external_id, '^api:[0-9]+:', '')
                = regexp_replace(m.external_id, '^api:[0-9]+:', '')
          )
          AND NOT EXISTS (
            SELECT 1 FROM meeting AS o
            WHERE o.external_id = regexp_replace(m.external_id, '^api:[0-9]+:', '')
          )
        """
    )
