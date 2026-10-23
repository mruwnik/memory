"""Claim ingest-trigger matches per content version, so on_edit triggers can fire on edits.

Revision ID: 20261027_trigger_match_versions
Revises: 20261026_external_mcp_servers
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261027_trigger_match_versions"
down_revision: Union[str, None] = "20261026_external_mcp_servers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# memory.agents.triggers.content_version as of this revision: sha256 over title,
# content and body (Slack's resolved_content, else content), joined by \x1f.
# Matching it keeps an on_edit trigger from taking a reprocess for an edit.
BACKFILL = """
UPDATE agent_trigger_matches m
SET content_hash = sha256(convert_to(concat_ws(chr(31),
    COALESCE(mm.subject, ce.event_title, c.title, bp.title, fp.title, gi.title,
             gd.title, mt.title, ''),
    COALESCE(s.content, ''),
    COALESCE(NULLIF(sm.resolved_content, ''), s.content, '')
), 'UTF8'))
FROM source_item s
LEFT JOIN mail_message mm ON mm.id = s.id
LEFT JOIN calendar_event ce ON ce.id = s.id
LEFT JOIN comic c ON c.id = s.id
LEFT JOIN blog_post bp ON bp.id = s.id
LEFT JOIN forum_post fp ON fp.id = s.id
LEFT JOIN github_item gi ON gi.id = s.id
LEFT JOIN google_doc gd ON gd.id = s.id
LEFT JOIN meeting mt ON mt.id = s.id
LEFT JOIN slack_message sm ON sm.id = s.id
WHERE s.id = m.item_id
"""


def upgrade() -> None:
    op.add_column(
        "agent_trigger_matches", sa.Column("content_hash", postgresql.BYTEA())
    )
    op.execute(BACKFILL)
    op.alter_column("agent_trigger_matches", "content_hash", nullable=False)
    op.drop_constraint(
        "agent_trigger_matches_pkey", "agent_trigger_matches", type_="primary"
    )
    op.create_primary_key(
        "agent_trigger_matches_pkey",
        "agent_trigger_matches",
        ["trigger_id", "item_id", "content_hash"],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM agent_trigger_matches m USING agent_trigger_matches newer "
        "WHERE newer.trigger_id = m.trigger_id AND newer.item_id = m.item_id "
        "AND (newer.created_at, newer.content_hash) > (m.created_at, m.content_hash)"
    )
    op.drop_constraint(
        "agent_trigger_matches_pkey", "agent_trigger_matches", type_="primary"
    )
    op.create_primary_key(
        "agent_trigger_matches_pkey", "agent_trigger_matches", ["trigger_id", "item_id"]
    )
    op.drop_column("agent_trigger_matches", "content_hash")
