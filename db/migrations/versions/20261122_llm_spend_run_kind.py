"""llm_spend gains `kind`, so purged agent runs can be folded in as history, and an
index on hour for the usage retention purge.

Revision ID: 20261122_llm_spend_run_kind
Revises: 20261121_llm_spend_usage
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "20261122_llm_spend_run_kind"
down_revision = "20261121_llm_spend_usage"
branch_labels = None
depends_on = None

BUCKET = ["provider_key_id", "user_id", "agent", "model", "hour"]


def bucket_index(columns: list[str]) -> None:
    op.create_index(
        "llm_spend_bucket",
        "llm_spend",
        columns,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("provider_key_id IS NOT NULL"),
    )


def upgrade():
    op.add_column(
        "llm_spend", sa.Column("kind", sa.Text(), nullable=False, server_default="memory")
    )
    op.create_check_constraint("llm_spend_kind", "llm_spend", "kind IN ('memory', 'run')")
    op.drop_index("llm_spend_bucket", table_name="llm_spend")
    bucket_index([*BUCKET, "kind"])
    op.create_index("llm_spend_hour", "llm_spend", ["hour"])


def downgrade():
    # Folded runs would count toward the daily caps as memory spend.
    op.execute("DELETE FROM llm_spend WHERE kind = 'run'")
    op.drop_index("llm_spend_hour", table_name="llm_spend")
    op.drop_index("llm_spend_bucket", table_name="llm_spend")
    bucket_index(BUCKET)
    op.drop_constraint("llm_spend_kind", "llm_spend", type_="check")
    op.drop_column("llm_spend", "kind")
