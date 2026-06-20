"""add github_item.author_person_id

Links a GitHub item's author login to the resolved Person, populated at ingest
(mirrors discord_message.author_id -> discord_users). Nullable: existing rows
stay NULL until re-synced or backfilled.

Revision ID: 20260620_github_author_person
Revises: 20260620_drop_github_teams
Create Date: 2026-06-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260620_github_author_person"
down_revision: Union[str, None] = "20260620_drop_github_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "github_item",
        sa.Column("author_person_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "github_item_author_person_id_fkey",
        "github_item",
        "people",
        ["author_person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "gh_author_person_id_idx", "github_item", ["author_person_id"]
    )


def downgrade() -> None:
    op.drop_index("gh_author_person_id_idx", table_name="github_item")
    op.drop_constraint(
        "github_item_author_person_id_fkey", "github_item", type_="foreignkey"
    )
    op.drop_column("github_item", "author_person_id")
