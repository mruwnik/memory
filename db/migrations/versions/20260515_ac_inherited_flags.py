"""Add inherited-flag columns for resolved access control.

``SourceItem.resolve_access_control()`` walks the data-source chain (Slack
channel -> workspace, email -> account) to resolve ``project_id`` /
``sensitivity``. Until now the resolved values were written only to Qdrant
payloads, never the SQL row, so inherited-only content was discoverable via
vector search but not via BM25 or direct row checks.

This adds ``project_id_inherited`` / ``sensitivity_inherited`` to every
``AccessControlMixin`` table (``source_item``, ``deadlines``). True means
the column holds a resolved (inherited) value the maintenance task may
overwrite; False means an explicit override that resolution must leave
alone.

Backfill:

* ``project_id``: current code never writes a *resolved* ``project_id`` to a
  row, so every existing non-NULL ``project_id`` is by definition an explicit
  override -> flag set False. Rows with ``project_id IS NULL`` keep the
  ``true`` default and are (correctly) re-resolved on the next maintenance
  run -- this is the BM25/vector-search asymmetry fix.

* ``sensitivity``: there is no SQL-visible way to tell an explicit
  ``sensitivity`` from one that merely equals a default. The column default
  is ``'basic'`` but per-class ``default_sensitivity`` varies (``BlogPost`` /
  ``Comic`` / ``BookSection`` / ``ForumPost`` default to ``'public'``), so a
  ``WHERE sensitivity <> 'basic'`` predicate would mis-classify inherited
  class defaults as explicit. Rather than guess, every *existing* row is
  flagged ``sensitivity_inherited = false`` -- i.e. its current stored
  sensitivity is frozen, exactly preserving today's behaviour with zero
  access-control widening. Only rows ingested *after* this migration get
  ``true`` (via the column default) and participate in sensitivity
  inheritance. The project_id fix -- the actual reported bug -- is unaffected.

No row-by-row resolution is performed here; inherited rows are reconciled
lazily by the maintenance task (eventual consistency).

Revision ID: 20260515_ac_inherited_flags
Revises: 20260508_add_deadlines
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260515_ac_inherited_flags"
down_revision: Union[str, None] = "20260508_add_deadlines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every table backed by AccessControlMixin.
TABLES: tuple[str, ...] = ("source_item", "deadlines")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "project_id_inherited",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "sensitivity_inherited",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )
        # An existing non-NULL project_id can only be an explicit override:
        # no code path writes a *resolved* project_id to the row today.
        op.execute(
            f"UPDATE {table} SET project_id_inherited = false "
            "WHERE project_id IS NOT NULL"
        )
        # Sensitivity: explicit vs inherited is not SQL-distinguishable (see
        # module docstring). Freeze every existing row's sensitivity as-is
        # rather than risk re-resolving it to a wider value.
        op.execute(f"UPDATE {table} SET sensitivity_inherited = false")


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "sensitivity_inherited")
        op.drop_column(table, "project_id_inherited")
