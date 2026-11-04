"""Drop the Claude Code containers runner's data.

Scheduled Claude sessions go (their executions cascade), with the runner's tables,
the default SSH key column, the claude_sessions scope and the module's stored flag.
Every step tolerates the object being gone already: older instances may never have
had it. The downgrade restores nothing.

Revision ID: 20261124_drop_claude_containers
Revises: 20261123_chunk_search_vector
Create Date: 2026-11-04
"""

from alembic import op

revision = "20261124_drop_claude_containers"
down_revision = "20261123_chunk_search_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM scheduled_tasks WHERE task_type = 'claude_session'")
    # Environments reference snapshots, so they go first.
    op.execute("DROP TABLE IF EXISTS claude_environments")
    op.execute("DROP TABLE IF EXISTS claude_config_snapshots")
    op.execute("DROP TABLE IF EXISTS claude_github_tokens")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS default_ssh_key_id")
    for table in ("users", "api_keys"):
        op.execute(
            f"UPDATE {table} SET scopes = array_remove(scopes, 'claude_sessions') "
            "WHERE 'claude_sessions' = ANY(scopes)"
        )
    op.execute("DELETE FROM instance_settings WHERE key = 'module_claude_containers'")


def downgrade() -> None:
    pass
