"""Add access control infrastructure.

This migration:
1. Creates an "owners" team for bootstrapping access control
2. Assigns all existing projects to the owners team
3. Adds the current user (via person) to the owners team as admin

This enables the visibility model where:
- Users see projects via: User → Person → team_members → Team → project_teams → Project
- Admin creates entity and can still see it (because they're in the owners team)

Revision ID: 20260201_access_control
Revises: 20260201_teams
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_access_control"
down_revision: Union[str, None] = "20260201_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Get connection for data migration
    conn = op.get_bind()

    # Step 1: Create the "owners" team
    # Use explicit ID 1 for predictable reference
    conn.execute(
        sa.text("""
            INSERT INTO teams (id, name, slug, description, tags)
            VALUES (1, 'Owners', 'owners', 'Default team with access to all existing projects', ARRAY['system']::text[])
            ON CONFLICT (slug) DO NOTHING
        """)
    )

    # Step 2: Find the first user's associated person (or create one if needed)
    # This is the admin/owner who should be in the owners team
    result = conn.execute(
        sa.text("""
            SELECT p.id
            FROM people p
            JOIN users u ON p.user_id = u.id
            ORDER BY u.id
            LIMIT 1
        """)
    ).fetchone()

    if result:
        person_id = result[0]
        # Add the owner to the owners team as admin
        conn.execute(
            sa.text("""
                INSERT INTO team_members (team_id, person_id, role)
                VALUES (1, :person_id, 'admin')
                ON CONFLICT (team_id, person_id) DO NOTHING
            """),
            {"person_id": person_id},
        )

    # Step 3: Assign all existing projects to the owners team
    conn.execute(
        sa.text("""
            INSERT INTO project_teams (project_id, team_id)
            SELECT id, 1 FROM projects
            ON CONFLICT (project_id, team_id) DO NOTHING
        """)
    )

    # Step 4: Reset the teams sequence to avoid conflicts with explicit ID
    conn.execute(
        sa.text("""
            SELECT setval('teams_id_seq', GREATEST((SELECT COALESCE(MAX(id), 0) FROM teams), 1))
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Remove all project-team associations with the owners team
    conn.execute(sa.text("DELETE FROM project_teams WHERE team_id = 1"))

    # Remove all members from the owners team
    conn.execute(sa.text("DELETE FROM team_members WHERE team_id = 1"))

    # Delete the owners team
    conn.execute(sa.text("DELETE FROM teams WHERE slug = 'owners'"))
