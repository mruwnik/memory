"""Add confidences

Revision ID: 152f8b4b52e8
Revises: ba301527a2eb
Create Date: 2025-06-03 11:56:42.302327

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "152f8b4b52e8"
down_revision: Union[str, None] = "ba301527a2eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "confidence_score",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_item_id", sa.BigInteger(), nullable=False),
        sa.Column("confidence_type", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.CheckConstraint("score >= 0.0 AND score <= 1.0", name="score_range_check"),
        sa.ForeignKeyConstraint(
            ["source_item_id"], ["source_item.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_item_id", "confidence_type", name="unique_source_confidence_type"
        ),
    )
    op.create_index("confidence_score_idx", "confidence_score", ["score"], unique=False)
    op.create_index(
        "confidence_source_idx", "confidence_score", ["source_item_id"], unique=False
    )
    op.create_index(
        "confidence_type_idx", "confidence_score", ["confidence_type"], unique=False
    )
    op.drop_index("agent_obs_confidence_idx", table_name="agent_observation")
    op.drop_column("agent_observation", "confidence")
    op.drop_index("note_confidence_idx", table_name="notes")
    op.drop_column("notes", "confidence")


def downgrade() -> None:
    op.add_column(
        "notes",
        sa.Column(
            "confidence",
            sa.NUMERIC(precision=3, scale=2),
            server_default=sa.text("0.5"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.create_index("note_confidence_idx", "notes", ["confidence"], unique=False)
    op.add_column(
        "agent_observation",
        sa.Column(
            "confidence",
            sa.NUMERIC(precision=3, scale=2),
            server_default=sa.text("0.5"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.create_index(
        "agent_obs_confidence_idx", "agent_observation", ["confidence"], unique=False
    )
    op.drop_index("confidence_type_idx", table_name="confidence_score")
    op.drop_index("confidence_source_idx", table_name="confidence_score")
    op.drop_index("confidence_score_idx", table_name="confidence_score")
    op.drop_table("confidence_score")
