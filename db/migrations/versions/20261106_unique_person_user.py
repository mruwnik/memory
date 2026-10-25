"""Link a user to at most one Person.

Revision ID: 20261106_unique_person_user
Revises: 20261105_unique_note_filename
Create Date: 2026-11-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_unique_person_user"
down_revision: Union[str, None] = "20261105_unique_note_filename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    duplicates = op.get_bind().execute(
        sa.text(
            "SELECT user_id, array_agg(identifier ORDER BY id) FROM people"
            " WHERE user_id IS NOT NULL GROUP BY user_id HAVING count(*) > 1"
        )
    ).fetchall()
    if duplicates:
        listing = "; ".join(f"user {user_id}: {', '.join(ids)}" for user_id, ids in duplicates)
        raise RuntimeError(
            "Some users are linked to more than one Person. Roll back, then merge"
            " those people or set user_id to NULL on all but one of each (see the"
            f" CHANGELOG's Manual steps) and update again. {listing}"
        )
    op.drop_index("person_user_idx", table_name="people")
    op.create_index(
        "person_user_idx",
        "people",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("person_user_idx", table_name="people")
    op.create_index("person_user_idx", "people", ["user_id"])
