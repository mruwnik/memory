"""Add unique constraint on github_repos (owner, name).

Previously, repos were unique per account, allowing the same GitHub repo
to be tracked multiple times by different accounts. This caused duplicate
projects when milestones were synced.

This migration:
1. Deletes duplicate projects (keeping ones with valid github_id)
2. Deletes duplicate github_repos entries (keeping ones with valid github_id)
3. Adds a unique index on (LOWER(owner), LOWER(name))

Revision ID: 20260204_unique_github_repos
Revises: 20260204_journal_entries
Create Date: 2026-02-04
"""

from alembic import op

revision = "20260204_unique_github_repos"
down_revision = "20260204_journal_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Delete duplicate projects
    # For each set of duplicate repos (same owner/name), keep the one with github_id set
    # and delete projects from the others
    op.execute("""
        DELETE FROM projects
        WHERE repo_id IN (
            SELECT gr.id
            FROM github_repos gr
            WHERE (gr.github_id IS NULL OR gr.github_id = 0)
            AND EXISTS (
                SELECT 1 FROM github_repos gr2
                WHERE LOWER(gr2.owner) = LOWER(gr.owner)
                AND LOWER(gr2.name) = LOWER(gr.name)
                AND gr2.id != gr.id
                AND gr2.github_id IS NOT NULL
                AND gr2.github_id > 0
            )
        )
    """)

    # Step 2: Delete duplicate github_repos entries
    # Keep the ones with valid github_id
    op.execute("""
        DELETE FROM github_repos
        WHERE (github_id IS NULL OR github_id = 0)
        AND EXISTS (
            SELECT 1 FROM github_repos gr2
            WHERE LOWER(gr2.owner) = LOWER(github_repos.owner)
            AND LOWER(gr2.name) = LOWER(github_repos.name)
            AND gr2.id != github_repos.id
            AND gr2.github_id IS NOT NULL
            AND gr2.github_id > 0
        )
    """)

    # Step 3: Add unique index on (owner, name) - case insensitive
    op.execute("""
        CREATE UNIQUE INDEX unique_github_repo_owner_name
        ON github_repos (LOWER(owner), LOWER(name))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS unique_github_repo_owner_name")
