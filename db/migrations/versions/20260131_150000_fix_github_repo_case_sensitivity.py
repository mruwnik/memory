"""Add github_id to github_repos and fix case-sensitive uniqueness.

GitHub org/repo names are case-insensitive, but our unique constraint
was case-sensitive, allowing duplicates like 'Equistamp' vs 'EquiStamp'.

This migration:
1. Adds github_id column to store GitHub's numeric repo ID
2. Merges duplicate repos (keeping the one with more items)
3. Adds a case-insensitive unique index to prevent future duplicates

Revision ID: 20260131_150000
Revises: 20260131_130000
Create Date: 2026-01-31

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "20260131_150000"
down_revision = "20260131_130000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add github_id column (nullable for now, will be backfilled by sync)
    op.add_column(
        "github_repos",
        sa.Column("github_id", sa.BigInteger(), nullable=True),
    )

    conn = op.get_bind()

    # Find duplicate repos (same account, same owner/name case-insensitive)
    duplicates = conn.execute(text("""
        SELECT
            LOWER(owner) as owner_lower,
            LOWER(name) as name_lower,
            account_id,
            array_agg(id ORDER BY id) as ids
        FROM github_repos
        GROUP BY account_id, LOWER(owner), LOWER(name)
        HAVING COUNT(*) > 1
    """)).fetchall()

    for row in duplicates:
        owner_lower, name_lower, account_id, ids = row
        print(f"Found duplicate repos for {owner_lower}/{name_lower}: {ids}")

        # Find which repo has more github_items
        item_counts = conn.execute(text("""
            SELECT repo_id, COUNT(*) as cnt
            FROM github_item
            WHERE repo_id = ANY(:ids)
            GROUP BY repo_id
        """), {"ids": ids}).fetchall()

        counts = {r[0]: r[1] for r in item_counts}
        # Keep the one with most items, or the newest if tied
        keep_id = max(ids, key=lambda x: (counts.get(x, 0), x))
        delete_ids = [i for i in ids if i != keep_id]

        print(f"  Keeping repo {keep_id} (items: {counts.get(keep_id, 0)})")
        print(f"  Merging from repos {delete_ids}")

        for delete_id in delete_ids:
            # Update github_items to point to the kept repo
            result = conn.execute(text("""
                UPDATE github_item
                SET repo_id = :keep_id
                WHERE repo_id = :delete_id
            """), {"keep_id": keep_id, "delete_id": delete_id})
            print(f"    Moved {result.rowcount} items from repo {delete_id}")

            # Update github_item.repo_path to use canonical casing
            # Get the canonical owner/name from the kept repo
            kept_repo = conn.execute(text("""
                SELECT owner, name FROM github_repos WHERE id = :keep_id
            """), {"keep_id": keep_id}).fetchone()
            if kept_repo:
                canonical_path = f"{kept_repo[0]}/{kept_repo[1]}"
                conn.execute(text("""
                    UPDATE github_item
                    SET repo_path = :path
                    WHERE repo_id = :keep_id
                """), {"path": canonical_path, "keep_id": keep_id})

            # Delete projects from the duplicate repo
            result = conn.execute(text("""
                DELETE FROM projects WHERE repo_id = :delete_id
            """), {"delete_id": delete_id})
            print(f"    Deleted {result.rowcount} projects from repo {delete_id}")

            # Delete the duplicate repo
            conn.execute(text("""
                DELETE FROM github_repos WHERE id = :delete_id
            """), {"delete_id": delete_id})
            print(f"    Deleted repo {delete_id}")

    # Drop the old case-sensitive unique constraint (if it exists)
    # The constraint may not exist on fresh databases where the model no longer defines it
    conn.execute(text("""
        ALTER TABLE github_repos
        DROP CONSTRAINT IF EXISTS unique_repo_per_account
    """))

    # Add case-insensitive unique index
    op.execute(text("""
        CREATE UNIQUE INDEX unique_repo_per_account_ci
        ON github_repos (account_id, LOWER(owner), LOWER(name))
    """))

    # Add unique index on github_id (partial - only where not null)
    op.execute(text("""
        CREATE UNIQUE INDEX unique_github_repo_id
        ON github_repos (github_id)
        WHERE github_id IS NOT NULL
    """))


def downgrade() -> None:
    # Drop the new indexes
    op.drop_index("unique_github_repo_id", "github_repos")
    op.drop_index("unique_repo_per_account_ci", "github_repos")

    # Restore the original case-sensitive constraint
    op.create_unique_constraint(
        "unique_repo_per_account",
        "github_repos",
        ["account_id", "owner", "name"],
    )

    # Remove the github_id column
    op.drop_column("github_repos", "github_id")
