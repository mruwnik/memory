"""Drop the per-user secrets table; scheduled Claude sessions use saved GitHub tokens.

Stored secrets are lost. Scheduled sessions lose their github_token /
github_token_write fields: they get the owner's saved clone token, and those
that had a write token get use_saved_write_token so they keep pushing with the
owner's saved write token.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261114_drop_secrets"
down_revision = "20261113_sync_schedule"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE scheduled_tasks SET data = jsonb_set("
        "data::jsonb, '{spawn_config,use_saved_write_token}', 'true')::json "
        "WHERE task_type = 'claude_session' "
        "AND coalesce(data::jsonb #>> '{spawn_config,github_token_write}', '') <> ''"
    )
    for field in ("github_token", "github_token_write"):
        op.execute(
            f"UPDATE scheduled_tasks SET data = (data::jsonb #- '{{spawn_config,{field}}}')::json "
            f"WHERE task_type = 'claude_session' AND data::jsonb -> 'spawn_config' ? '{field}'"
        )
    op.drop_index("ix_secrets_name", table_name="secrets")
    op.drop_table("secrets")


def downgrade():
    op.create_table(
        "secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="unique_secret_per_user"),
    )
    op.create_index("ix_secrets_name", "secrets", ["name"])
