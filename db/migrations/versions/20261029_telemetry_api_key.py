"""Record which API key sent each telemetry event.

Revision ID: 20261029_telemetry_api_key
Revises: 20261028_claude_sessions_scope
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_telemetry_api_key"
down_revision: Union[str, None] = "20261028_claude_sessions_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The FK goes on NOT VALID and is validated separately, and the index is
    # built concurrently, so neither blocks telemetry writes on a large table.
    op.add_column("telemetry_events", sa.Column("api_key_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "telemetry_events_api_key_id_fkey",
        "telemetry_events",
        "api_keys",
        ["api_key_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE telemetry_events VALIDATE CONSTRAINT telemetry_events_api_key_id_fkey"
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_telemetry_events_api_key",
            "telemetry_events",
            ["api_key_id", "timestamp"],
            postgresql_where=sa.text("api_key_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_telemetry_events_api_key",
            table_name="telemetry_events",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint("telemetry_events_api_key_id_fkey", "telemetry_events", type_="foreignkey")
    op.drop_column("telemetry_events", "api_key_id")
