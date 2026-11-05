"""Deleting a source item's subclass row deletes its source_item row.

Subclass tables (mail_message, book_section, ...) can lose rows to their own
ON DELETE CASCADE foreign keys (deleting an email account, a book, a Slack
channel). That left the source_item row, its chunks and vectors behind, and
any search that hit it failed. Each table that inherits source_item's id now
has a trigger that deletes the source_item row once no subclass row has its id.

The trigger is deferred to commit, so an ORM delete, which removes the subclass
row and then the source_item row itself, finds nothing left to delete.

A table added later needs the same trigger in its own migration:

    CREATE CONSTRAINT TRIGGER delete_orphaned_source_item AFTER DELETE ON <table>
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION delete_orphaned_source_item()

Rows orphaned before this migration are removed by the maintenance task
``delete_orphaned_source_items``.

Revision ID: 20261128_orphaned_source_items
Revises: 20261127_discord_realtime
Create Date: 2026-11-05
"""

from alembic import op
from sqlalchemy import text

revision = "20261128_orphaned_source_items"
down_revision = "20261127_discord_realtime"
branch_labels = None
depends_on = None

# Tables whose primary key is a single `id` column referencing source_item.id.
SUBCLASS_TABLES = """
SELECT fk.conrelid::regclass::text
FROM pg_constraint fk
JOIN pg_attribute a ON a.attrelid = fk.conrelid AND a.attnum = fk.conkey[1]
JOIN pg_constraint pk ON pk.conrelid = fk.conrelid AND pk.contype = 'p' AND pk.conkey = fk.conkey
WHERE fk.contype = 'f'
  AND fk.confrelid = 'source_item'::regclass
  AND cardinality(fk.conkey) = 1
  AND a.attname = 'id'
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION delete_orphaned_source_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            EXECUTE format(
                'DELETE FROM source_item s WHERE s.id = $1 '
                'AND NOT EXISTS (SELECT 1 FROM %s c WHERE c.id = s.id)',
                TG_RELID::regclass
            ) USING OLD.id;
            RETURN NULL;
        END
        $$
        """
    )
    for (table,) in op.get_bind().execute(text(SUBCLASS_TABLES)):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER delete_orphaned_source_item AFTER DELETE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION delete_orphaned_source_item()"
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION delete_orphaned_source_item() CASCADE")
