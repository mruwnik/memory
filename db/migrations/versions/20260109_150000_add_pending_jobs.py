"""Add pending_jobs table for async job tracking.

Creates the pending_jobs table for tracking client-facing async operations
like meeting processing, content reprocessing, etc.

Revision ID: 20260109_150000
Revises: 20260109_120000
Create Date: 2026-01-09

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260109_150000"
down_revision = "20260109_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # Job identification
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("celery_task_id", sa.String(200), nullable=True),
        # Status tracking
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Result linking
        sa.Column("result_id", sa.BigInteger(), nullable=True),
        sa.Column("result_type", sa.String(50), nullable=True),
        # Job parameters
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Retry tracking
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        # User association
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )

    # Create indexes for common query patterns
    op.create_index("idx_pending_jobs_status", "pending_jobs", ["status"])
    op.create_index("idx_pending_jobs_job_type", "pending_jobs", ["job_type"])
    op.create_index("idx_pending_jobs_external_id", "pending_jobs", ["external_id"])
    op.create_index("idx_pending_jobs_user_id", "pending_jobs", ["user_id"])
    op.create_index("idx_pending_jobs_created_at", "pending_jobs", ["created_at"])
    op.create_index(
        "idx_pending_jobs_celery_task_id", "pending_jobs", ["celery_task_id"]
    )


def downgrade() -> None:
    op.drop_table("pending_jobs")
