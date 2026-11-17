"""Flag source items whose Qdrant access-control payload may be stale.

Adding the column is metadata-only. The backfill flags the rows the old sweep
rewrote every run, those with an explicit project or sensitivity, since a
sweep that hit its time limit may never have reached some of them, plus rows
changed in the last day. The next daily sweep writes each of them once.

Revision ID: 20270103_access_payload_stale
Revises: 20270102_github_user_by_id
Create Date: 2026-11-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20270103_access_payload_stale"
down_revision: Union[str, None] = "20270102_github_user_by_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_item",
        sa.Column(
            "access_payload_stale",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE source_item SET access_payload_stale = true "
        "WHERE NOT project_id_inherited OR NOT sensitivity_inherited "
        "OR updated_at > now() - interval '1 day'"
    )


def downgrade() -> None:
    op.drop_column("source_item", "access_payload_stale")
