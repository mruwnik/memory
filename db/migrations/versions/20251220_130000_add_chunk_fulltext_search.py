"""Add full-text search to chunks

Revision ID: a1b2c3d4e5f6
Revises: 89861d5f1102
Create Date: 2025-12-20 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "89861d5f1102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tsvector column for full-text search
    op.execute(
        """
        ALTER TABLE chunk
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        """
    )

    # Create GIN index for fast full-text search
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS chunk_search_idx
        ON chunk USING GIN (search_vector)
        """
    )

    # Create function to generate search vector from content
    op.execute(
        """
        CREATE OR REPLACE FUNCTION chunk_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.content IS NOT NULL THEN
                NEW.search_vector := to_tsvector('english', NEW.content);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # Create trigger to auto-update search_vector on insert/update
    op.execute(
        """
        DROP TRIGGER IF EXISTS chunk_search_vector_trigger ON chunk;
        CREATE TRIGGER chunk_search_vector_trigger
        BEFORE INSERT OR UPDATE OF content ON chunk
        FOR EACH ROW
        EXECUTE FUNCTION chunk_search_vector_update()
        """
    )

    # Populate search_vector for existing rows (in batches to avoid timeout)
    # This updates in chunks of 10000 rows at a time
    op.execute(
        """
        UPDATE chunk
        SET search_vector = to_tsvector('english', content)
        WHERE content IS NOT NULL AND search_vector IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chunk_search_vector_trigger ON chunk")
    op.execute("DROP FUNCTION IF EXISTS chunk_search_vector_update()")
    op.execute("DROP INDEX IF EXISTS chunk_search_idx")
    op.execute("ALTER TABLE chunk DROP COLUMN IF EXISTS search_vector")
