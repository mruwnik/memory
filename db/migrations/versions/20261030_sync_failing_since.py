"""Track how long each source's syncs have been failing.

Revision ID: 20261030_sync_failing_since
Revises: 20261029_telemetry_api_key
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261030_sync_failing_since"
down_revision: Union[str, None] = "20261029_telemetry_api_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES_WITH_SYNC_ERROR = [
    "email_accounts",
    "google_accounts",
    "calendar_accounts",
    "transcript_accounts",
    "slack_workspaces",
]
TABLES_WITHOUT_SYNC_ERROR = ["google_folders", "github_repos", "article_feeds"]


def upgrade() -> None:
    for table in TABLES_WITHOUT_SYNC_ERROR:
        op.add_column(table, sa.Column("sync_error", sa.Text(), nullable=True))
    for table in TABLES_WITH_SYNC_ERROR + TABLES_WITHOUT_SYNC_ERROR:
        op.add_column(
            table, sa.Column("sync_failing_since", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    for table in TABLES_WITH_SYNC_ERROR + TABLES_WITHOUT_SYNC_ERROR:
        op.drop_column(table, "sync_failing_since")
    for table in TABLES_WITHOUT_SYNC_ERROR:
        op.drop_column(table, "sync_error")
