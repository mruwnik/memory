"""Add session and project tables.

Creates tables for storing coding sessions and projects.
Session messages are stored as JSONL files on disk.

Revision ID: 20260112_120000
Revises: 20260111_150000
Create Date: 2026-01-12

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260112_120000"
down_revision = "20260111_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create projects table
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("directory", sa.Text(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "directory", name="unique_user_project"),
    )

    op.create_index("idx_projects_user", "projects", ["user_id"])
    op.create_index("idx_projects_directory", "projects", ["directory"])
    op.create_index("idx_projects_source", "projects", ["source"])

    # Create sessions table (UUID as primary key)
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("git_branch", sa.String(255), nullable=True),
        sa.Column("tool_version", sa.String(50), nullable=True),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("transcript_path", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_session_id"], ["sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_sessions_user", "sessions", ["user_id"])
    op.create_index("idx_sessions_project", "sessions", ["project_id"])
    op.create_index("idx_sessions_parent", "sessions", ["parent_session_id"])
    op.create_index("idx_sessions_started", "sessions", ["started_at"])
    op.create_index("idx_sessions_ended", "sessions", ["ended_at"])
    op.create_index("idx_sessions_source", "sessions", ["source"])


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_table("projects")
