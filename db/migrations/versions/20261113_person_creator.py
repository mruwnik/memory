"""Record who created each Person, and whether they were trusted (ingest or an admin).

Existing people count as trusted, so a new user may still be linked to them by email.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261113_person_creator"
down_revision = "20261112_forum_comments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "people",
        sa.Column(
            "creator_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "people",
        sa.Column("creator_trusted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade():
    op.drop_column("people", "creator_trusted")
    op.drop_column("people", "creator_id")
