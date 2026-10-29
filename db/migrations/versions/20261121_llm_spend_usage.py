"""llm_spend buckets by the user spent for and the model, with token counts; they
outlive their key and user.

Existing buckets keep a NULL user and model: who they were spent for wasn't recorded.

Revision ID: 20261121_llm_spend_usage
Revises: 20261119_email_sender_tags
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "20261121_llm_spend_usage"
down_revision = "20261120_audit_events"
branch_labels = None
depends_on = None

TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def upgrade():
    op.add_column(
        "llm_spend",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.add_column("llm_spend", sa.Column("model", sa.Text(), nullable=True))
    for name in TOKENS:
        op.add_column(
            "llm_spend", sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
        )
    op.create_index("ix_llm_spend_user_id", "llm_spend", ["user_id"])

    op.drop_constraint("llm_spend_bucket", "llm_spend", type_="unique")
    op.create_index(
        "llm_spend_bucket",
        "llm_spend",
        ["provider_key_id", "user_id", "agent", "model", "hour"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("provider_key_id IS NOT NULL"),
    )

    op.alter_column("llm_spend", "provider_key_id", nullable=True)
    op.drop_constraint("llm_spend_provider_key_id_fkey", "llm_spend", type_="foreignkey")
    op.create_foreign_key(
        "llm_spend_provider_key_id_fkey",
        "llm_spend",
        "llm_provider_keys",
        ["provider_key_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    # The old table can't hold buckets without a key, and its buckets were per key,
    # agent and hour only: drop the orphans and merge the rest into one row each.
    op.execute("DELETE FROM llm_spend WHERE provider_key_id IS NULL")
    op.execute(
        """
        WITH totals AS (
            SELECT min(id) AS id, sum(cost_usd) AS cost_usd, sum(calls) AS calls
            FROM llm_spend GROUP BY provider_key_id, agent, hour
        )
        UPDATE llm_spend s SET cost_usd = t.cost_usd, calls = t.calls
        FROM totals t WHERE s.id = t.id
        """
    )
    op.execute(
        "DELETE FROM llm_spend WHERE id NOT IN "
        "(SELECT min(id) FROM llm_spend GROUP BY provider_key_id, agent, hour)"
    )

    op.drop_constraint("llm_spend_provider_key_id_fkey", "llm_spend", type_="foreignkey")
    op.create_foreign_key(
        "llm_spend_provider_key_id_fkey",
        "llm_spend",
        "llm_provider_keys",
        ["provider_key_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("llm_spend", "provider_key_id", nullable=False)

    op.drop_index("llm_spend_bucket", table_name="llm_spend")
    op.create_unique_constraint(
        "llm_spend_bucket", "llm_spend", ["provider_key_id", "agent", "hour"]
    )
    op.drop_index("ix_llm_spend_user_id", table_name="llm_spend")
    for name in TOKENS:
        op.drop_column("llm_spend", name)
    op.drop_column("llm_spend", "model")
    op.drop_column("llm_spend", "user_id")
