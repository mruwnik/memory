"""discord schedules

Revision ID: 2fb3223dc71b
Revises: 1d6bc8015ea9
Create Date: 2025-08-12 23:43:27.671182

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2fb3223dc71b"
down_revision: Union[str, None] = "1d6bc8015ea9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_llm_calls",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("scheduled_time", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("discord_channel", sa.String(), nullable=True),
        sa.Column("discord_user", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("celery_task_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("users", sa.Column("discord_user_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "discord_user_id")
    op.drop_table("scheduled_llm_calls")
