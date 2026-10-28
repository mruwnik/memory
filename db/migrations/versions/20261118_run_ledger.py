"""agent_runs as the run ledger: agent slug, executor, parent and finished indexes; runs expire.

Runs started from a prebuilt or system agent move their slug from
trigger_ref.agent to the new agent column. steps_purged_at goes: the purge now
deletes whole runs past their retention, so a stored run always has its steps.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261118_run_ledger"
down_revision = "20261117_hash_session_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("agent", sa.Text(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("executor", sa.String(64), nullable=False, server_default="server"),
    )
    op.execute(
        "UPDATE agent_runs SET agent = trigger_ref->>'agent', trigger_ref = trigger_ref - 'agent' "
        "WHERE trigger_ref ? 'agent'"
    )
    op.create_index("agent_runs_parent_idx", "agent_runs", ["parent_run_id"])
    op.create_index("agent_runs_finished_idx", "agent_runs", ["finished_at"])
    op.drop_column("agent_runs", "steps_purged_at")


def downgrade():
    op.add_column(
        "agent_runs",
        sa.Column("steps_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("agent_runs_finished_idx", table_name="agent_runs")
    op.drop_index("agent_runs_parent_idx", table_name="agent_runs")
    op.execute(
        "UPDATE agent_runs SET trigger_ref = trigger_ref || jsonb_build_object('agent', agent) "
        "WHERE agent IS NOT NULL"
    )
    op.drop_column("agent_runs", "executor")
    op.drop_column("agent_runs", "agent")
