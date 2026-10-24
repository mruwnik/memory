"""Grant the new `claude_sessions` scope to users who could already spawn.

Spawning needs a Claude config snapshot or environment the user owns, so
those owners keep access. `*` holders pass without it. API keys with their
own scope list are not granted it: a narrow key reaching the containers is
what the scope closes; keys that inherit the user's scopes keep working.

Revision ID: 20261028_claude_sessions_scope
Revises: 20261028_source_item_creators
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261028_claude_sessions_scope"
down_revision: Union[str, None] = "20261028_source_item_creators"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET scopes = array_append(scopes, 'claude_sessions') "
        "WHERE NOT 'claude_sessions' = ANY(scopes) AND NOT '*' = ANY(scopes) AND ("
        "id IN (SELECT user_id FROM claude_config_snapshots) "
        "OR id IN (SELECT user_id FROM claude_environments))"
    )


def downgrade() -> None:
    for table in ("users", "api_keys"):
        op.execute(
            f"UPDATE {table} SET scopes = array_remove(scopes, 'claude_sessions') "
            "WHERE 'claude_sessions' = ANY(scopes)"
        )
