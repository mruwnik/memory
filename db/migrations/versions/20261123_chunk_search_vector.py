"""chunk.search_vector is kept current by a trigger and GIN-indexed.

Existing rows are filled by the `maintenance backfill-search-vectors` post-deploy
task, in batches, rather than here: one UPDATE (or a generated column) would
rewrite the whole chunk table under a lock.

Revision ID: 20261123_chunk_search_vector
Revises: 20261122_llm_spend_run_kind
Create Date: 2026-10-29
"""

from alembic import op

revision = "20261123_chunk_search_vector"
down_revision = "20261122_llm_spend_run_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 'english' must match what bm25.py queries with. A tsvector over 1MB (bytes,
    # not characters) raises, which would fail the insert of a chunk holding a
    # whole document. 500k characters fits most text; multibyte text that still
    # overflows falls back to 200k characters, under 1MB at 4 bytes each.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION chunk_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', left(NEW.content, 500000));
            RETURN NEW;
        EXCEPTION WHEN program_limit_exceeded THEN
            NEW.search_vector := to_tsvector('english', left(NEW.content, 200000));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    # LOCAL, so the autocommit_block's commit clears it before the concurrent
    # index build, which waits on older transactions.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        """
        CREATE OR REPLACE TRIGGER chunk_search_vector_trigger
        BEFORE INSERT OR UPDATE OF content ON chunk
        FOR EACH ROW EXECUTE FUNCTION chunk_search_vector_update()
        """
    )
    with op.get_context().autocommit_block():
        # A failed concurrent build leaves an invalid index behind, so drop it first.
        op.drop_index(
            "chunk_search_vector_idx",
            table_name="chunk",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "chunk_search_vector_idx",
            "chunk",
            ["search_vector"],
            postgresql_using="gin",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "chunk_search_vector_idx",
            table_name="chunk",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.execute("DROP TRIGGER IF EXISTS chunk_search_vector_trigger ON chunk")
    op.execute("DROP FUNCTION IF EXISTS chunk_search_vector_update()")
