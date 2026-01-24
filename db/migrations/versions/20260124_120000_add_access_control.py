"""Add access control v2: GitHub milestones as projects.

This migration implements project-based RBAC where:
- Projects are GitHub milestones (not a separate table)
- Access is Person-based via project_collaborators junction table
- User -> Person -> project_collaborators -> GithubMilestone
- Superadmins are users with admin scope (checked via has_admin_scope())

Tables created:
- github_users: GitHub user accounts linked to Persons
- project_collaborators: Junction table linking Persons to milestones with roles
- access_logs: Audit logging for access events

Columns added:
- discord_channels.project_id, .sensitivity
- slack_channels.project_id, .sensitivity
- source_item.project_id, .sensitivity, .people

Revision ID: 20260124_120000
Revises: 20260123_120000
Create Date: 2026-01-24 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision: str = "20260124_120000"
down_revision: Union[str, None] = "20260123_120000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create github_users table
    op.create_table(
        "github_users",
        sa.Column("id", sa.BigInteger(), nullable=False),  # GitHub user ID
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("person_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("username", name="unique_github_username"),
    )
    op.create_index("github_users_username_idx", "github_users", ["username"])
    op.create_index("github_users_person_idx", "github_users", ["person_id"])

    # Create project_collaborators junction table
    op.create_table(
        "project_collaborators",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="contributor"),
        sa.PrimaryKeyConstraint("project_id", "person_id"),
        sa.ForeignKeyConstraint(["project_id"], ["github_milestones.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('contributor', 'manager', 'admin')", name="valid_collaborator_role"),
    )
    op.create_index("project_collaborators_project_idx", "project_collaborators", ["project_id"])
    op.create_index("project_collaborators_person_idx", "project_collaborators", ["person_id"])

    # Create access_logs table
    op.create_table(
        "access_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("item_id", sa.BigInteger(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_access_logs_user_time", "access_logs", ["user_id", "timestamp"])
    op.create_index("idx_access_logs_time", "access_logs", ["timestamp"])
    op.execute(
        "CREATE INDEX idx_access_logs_item ON access_logs (item_id) WHERE item_id IS NOT NULL"
    )

    # Add project_id and sensitivity to discord_channels
    op.add_column(
        "discord_channels",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "discord_channels",
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"),
    )
    op.create_foreign_key(
        "fk_discord_channels_project",
        "discord_channels",
        "github_milestones",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "valid_discord_channel_sensitivity",
        "discord_channels",
        "sensitivity IN ('basic', 'internal', 'confidential')",
    )
    op.create_index("discord_channels_project_idx", "discord_channels", ["project_id"])

    # Add project_id and sensitivity to slack_channels
    op.add_column(
        "slack_channels",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "slack_channels",
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"),
    )
    op.create_foreign_key(
        "fk_slack_channels_project",
        "slack_channels",
        "github_milestones",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "valid_slack_channel_sensitivity",
        "slack_channels",
        "sensitivity IN ('basic', 'internal', 'confidential')",
    )
    op.create_index("slack_channels_project_idx", "slack_channels", ["project_id"])

    # Add project_id, sensitivity, and people to source_item
    op.add_column(
        "source_item",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "source_item",
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"),
    )
    op.add_column(
        "source_item",
        sa.Column("people", ARRAY(sa.BigInteger()), nullable=True),
    )
    op.create_foreign_key(
        "fk_source_item_project",
        "source_item",
        "github_milestones",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "valid_sensitivity_level",
        "source_item",
        "sensitivity IN ('basic', 'internal', 'confidential')",
    )
    op.create_index("source_project_idx", "source_item", ["project_id"])
    op.create_index("source_sensitivity_idx", "source_item", ["sensitivity"])
    op.create_index("source_people_idx", "source_item", ["people"], postgresql_using="gin")


def downgrade() -> None:
    # Remove indexes and columns from source_item
    op.drop_index("source_people_idx", table_name="source_item")
    op.drop_index("source_sensitivity_idx", table_name="source_item")
    op.drop_index("source_project_idx", table_name="source_item")
    op.drop_constraint("valid_sensitivity_level", "source_item", type_="check")
    op.drop_constraint("fk_source_item_project", "source_item", type_="foreignkey")
    op.drop_column("source_item", "people")
    op.drop_column("source_item", "sensitivity")
    op.drop_column("source_item", "project_id")

    # Remove from slack_channels
    op.drop_index("slack_channels_project_idx", table_name="slack_channels")
    op.drop_constraint("valid_slack_channel_sensitivity", "slack_channels", type_="check")
    op.drop_constraint("fk_slack_channels_project", "slack_channels", type_="foreignkey")
    op.drop_column("slack_channels", "sensitivity")
    op.drop_column("slack_channels", "project_id")

    # Remove from discord_channels
    op.drop_index("discord_channels_project_idx", table_name="discord_channels")
    op.drop_constraint("valid_discord_channel_sensitivity", "discord_channels", type_="check")
    op.drop_constraint("fk_discord_channels_project", "discord_channels", type_="foreignkey")
    op.drop_column("discord_channels", "sensitivity")
    op.drop_column("discord_channels", "project_id")

    # Drop access_logs
    op.execute("DROP INDEX IF EXISTS idx_access_logs_item")
    op.drop_index("idx_access_logs_time", table_name="access_logs")
    op.drop_index("idx_access_logs_user_time", table_name="access_logs")
    op.drop_table("access_logs")

    # Drop project_collaborators
    op.drop_index("project_collaborators_person_idx", table_name="project_collaborators")
    op.drop_index("project_collaborators_project_idx", table_name="project_collaborators")
    op.drop_table("project_collaborators")

    # Drop github_users
    op.drop_index("github_users_person_idx", table_name="github_users")
    op.drop_index("github_users_username_idx", table_name="github_users")
    op.drop_table("github_users")
