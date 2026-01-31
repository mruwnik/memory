"""Add category_id to discord_channels and rename github_milestones to projects.

1. Adds category_id to discord_channels to track which Discord category a channel
   belongs to (snowflake ID, not a FK - category metadata fetched from Discord on demand).

2. Renames github_milestones table to projects - it's now the central Project entity
   for access control, collaborators, and organization hierarchy.

3. Renames existing 'projects' table (for coding sessions) to 'coding_projects'
   to free up the 'projects' name.

Revision ID: 20260131_150000
Revises: 20260131_120000
Create Date: 2026-01-31

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260131_150000"
down_revision = "20260131_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add category_id to discord_channels
    op.add_column(
        "discord_channels",
        sa.Column("category_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "discord_channels_category_idx",
        "discord_channels",
        ["category_id"],
    )

    # 2. Rename existing 'projects' table (coding sessions) to 'coding_projects'
    op.rename_table("projects", "coding_projects")

    # Rename coding_projects indexes
    op.execute("ALTER INDEX IF EXISTS idx_projects_user RENAME TO idx_coding_projects_user")
    op.execute("ALTER INDEX IF EXISTS idx_projects_directory RENAME TO idx_coding_projects_directory")
    op.execute("ALTER INDEX IF EXISTS idx_projects_source RENAME TO idx_coding_projects_source")

    # Rename constraint
    op.execute("ALTER TABLE coding_projects RENAME CONSTRAINT unique_user_project TO unique_user_coding_project")

    # Rename sequence
    op.execute("ALTER SEQUENCE IF EXISTS projects_id_seq RENAME TO coding_projects_id_seq")

    # Update sessions FK column name from project_id to coding_project_id
    op.alter_column("sessions", "project_id", new_column_name="coding_project_id")

    # Rename session index
    op.execute("ALTER INDEX IF EXISTS idx_sessions_project RENAME TO idx_sessions_coding_project")

    # 3. Rename github_milestones to projects
    op.rename_table("github_milestones", "projects")

    # Rename github_milestones indexes
    op.execute("ALTER INDEX IF EXISTS github_milestones_repo_idx RENAME TO projects_repo_idx")
    op.execute("ALTER INDEX IF EXISTS github_milestones_github_id_idx RENAME TO projects_github_id_idx")
    op.execute("ALTER INDEX IF EXISTS github_milestones_parent_idx RENAME TO projects_parent_idx")

    # Rename sequence
    op.execute("ALTER SEQUENCE IF EXISTS github_milestones_id_seq RENAME TO projects_id_seq")


def downgrade() -> None:
    # 1. Rename projects back to github_milestones
    op.rename_table("projects", "github_milestones")

    op.execute("ALTER INDEX IF EXISTS projects_repo_idx RENAME TO github_milestones_repo_idx")
    op.execute("ALTER INDEX IF EXISTS projects_github_id_idx RENAME TO github_milestones_github_id_idx")
    op.execute("ALTER INDEX IF EXISTS projects_parent_idx RENAME TO github_milestones_parent_idx")

    op.execute("ALTER SEQUENCE IF EXISTS projects_id_seq RENAME TO github_milestones_id_seq")

    # 2. Rename coding_projects back to projects
    # First rename sessions FK column back
    op.alter_column("sessions", "coding_project_id", new_column_name="project_id")
    op.execute("ALTER INDEX IF EXISTS idx_sessions_coding_project RENAME TO idx_sessions_project")

    op.rename_table("coding_projects", "projects")

    op.execute("ALTER INDEX IF EXISTS idx_coding_projects_user RENAME TO idx_projects_user")
    op.execute("ALTER INDEX IF EXISTS idx_coding_projects_directory RENAME TO idx_projects_directory")
    op.execute("ALTER INDEX IF EXISTS idx_coding_projects_source RENAME TO idx_projects_source")

    op.execute("ALTER TABLE projects RENAME CONSTRAINT unique_user_coding_project TO unique_user_project")

    op.execute("ALTER SEQUENCE IF EXISTS coding_projects_id_seq RENAME TO projects_id_seq")

    # 3. Remove category_id from discord_channels
    op.drop_index("discord_channels_category_idx", table_name="discord_channels")
    op.drop_column("discord_channels", "category_id")
