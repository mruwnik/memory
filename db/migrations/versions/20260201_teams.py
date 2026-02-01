"""Add teams for access control and drop project_collaborators.

This migration:
1. Creates teams table
2. Creates team_members junction table (team <-> person)
3. Creates project_teams junction table (project <-> team)
4. Adds contributor_status to people table
5. Drops project_collaborators table (replaced by team-based access)

Revision ID: 20260201_teams
Revises: 20260201_person_tidbit
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_teams"
down_revision: Union[str, None] = "20260201_person_tidbit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Create teams table
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        # Discord integration
        sa.Column("discord_role_id", sa.BigInteger(), nullable=True),
        sa.Column("discord_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("auto_sync_discord", sa.Boolean(), nullable=False, server_default="true"),
        # GitHub integration
        sa.Column("github_team_id", sa.BigInteger(), nullable=True),
        sa.Column("github_team_slug", sa.Text(), nullable=True),
        sa.Column("github_org", sa.Text(), nullable=True),
        sa.Column("auto_sync_github", sa.Boolean(), nullable=False, server_default="true"),
        # Lifecycle
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("teams_slug_idx", "teams", ["slug"])
    op.create_index("teams_tags_idx", "teams", ["tags"], postgresql_using="gin")
    op.create_index("teams_is_active_idx", "teams", ["is_active"])

    # Step 2: Create team_members junction table
    op.create_table(
        "team_members",
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('member', 'lead', 'admin')",
            name="valid_team_member_role",
        ),
    )
    op.create_index("team_members_team_idx", "team_members", ["team_id"])
    op.create_index("team_members_person_idx", "team_members", ["person_id"])

    # Step 3: Create project_teams junction table
    op.create_table(
        "project_teams",
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("project_teams_project_idx", "project_teams", ["project_id"])
    op.create_index("project_teams_team_idx", "project_teams", ["team_id"])

    # Step 4: Add contributor_status to people table
    op.add_column(
        "people",
        sa.Column(
            "contributor_status",
            sa.String(50),
            nullable=False,
            server_default="contractor",
        ),
    )

    # Step 5: Drop project_collaborators table (replaced by team-based access)
    op.drop_index("project_collaborators_person_idx", table_name="project_collaborators")
    op.drop_index("project_collaborators_project_idx", table_name="project_collaborators")
    op.drop_table("project_collaborators")


def downgrade() -> None:
    # Recreate project_collaborators table
    op.create_table(
        "project_collaborators",
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(50), nullable=False, server_default="contributor"),
        sa.CheckConstraint(
            "role IN ('contributor', 'manager', 'admin')",
            name="valid_collaborator_role",
        ),
    )
    op.create_index(
        "project_collaborators_project_idx", "project_collaborators", ["project_id"]
    )
    op.create_index(
        "project_collaborators_person_idx", "project_collaborators", ["person_id"]
    )

    # Remove contributor_status from people
    op.drop_column("people", "contributor_status")

    # Drop project_teams
    op.drop_index("project_teams_team_idx", table_name="project_teams")
    op.drop_index("project_teams_project_idx", table_name="project_teams")
    op.drop_table("project_teams")

    # Drop team_members
    op.drop_index("team_members_person_idx", table_name="team_members")
    op.drop_index("team_members_team_idx", table_name="team_members")
    op.drop_table("team_members")

    # Drop teams
    op.drop_index("teams_is_active_idx", table_name="teams")
    op.drop_index("teams_tags_idx", table_name="teams")
    op.drop_index("teams_slug_idx", table_name="teams")
    op.drop_table("teams")
