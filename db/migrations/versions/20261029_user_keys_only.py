"""Agent runs pay only with users' own provider keys.

Definitions pinned to the server's key move to their owner's default key for
the model's provider; the rest lose the pin, so they use the owner's default
key once one exists and are refused with "add an API key" until then. The
llm_server_key scope and agent_runs.key_source go: every run is on a user's key.

Downgrade is lossy: the scope is not restored and key_source is backfilled as
'user' for every run.

Revision ID: 20261029_user_keys_only
Revises: 20261102_drop_team_sync
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_user_keys_only"
down_revision: Union[str, None] = "20261102_drop_team_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PIN_SERVER_DEFINITIONS = sa.text(
    """
    UPDATE agent_definitions d
    SET spec = jsonb_set(d.spec, '{provider_key}', to_jsonb(k.id))
    FROM llm_provider_keys k
    WHERE d.spec->>'provider_key' = 'server'
      AND k.user_id = d.user_id
      AND k.is_default
      AND k.provider = split_part(
        COALESCE(d.spec->>'model', 'anthropic/claude-sonnet-5-5'), '/', 1
      )
    """
)

UNPIN_SERVER_DEFINITIONS = sa.text(
    "UPDATE agent_definitions SET spec = spec - 'provider_key' "
    "WHERE spec->>'provider_key' = 'server'"
)


def pin_server_definitions(bind) -> None:
    bind.execute(PIN_SERVER_DEFINITIONS)
    bind.execute(UNPIN_SERVER_DEFINITIONS)


def upgrade() -> None:
    pin_server_definitions(op.get_bind())
    for table in ("users", "api_keys"):
        op.execute(
            f"UPDATE {table} SET scopes = array_remove(scopes, 'llm_server_key') "
            "WHERE 'llm_server_key' = ANY(scopes)"
        )
    op.drop_constraint("agent_runs_key_source_check", "agent_runs", type_="check")
    op.drop_column("agent_runs", "key_source")


def downgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("key_source", sa.String(10), nullable=False, server_default="user"),
    )
    op.create_check_constraint(
        "agent_runs_key_source_check", "agent_runs", "key_source IN ('server','user')"
    )
