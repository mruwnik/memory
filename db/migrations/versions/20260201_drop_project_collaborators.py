"""Drop project_collaborators table.

Team-based access control replaces direct project collaborators.
Access is now managed via teams -> project_teams.

Revision ID: 20260201_drop_collaborators
Revises: 20260201_teams
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_drop_collaborators"
down_revision: Union[str, None] = "20260201_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop indexes first
    op.drop_index("project_collaborators_person_idx", table_name="project_collaborators")
    op.drop_index("project_collaborators_project_idx", table_name="project_collaborators")

    # Drop the table
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
