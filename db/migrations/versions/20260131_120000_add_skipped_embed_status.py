"""Add SKIPPED to embed_status enum.

Content that is intentionally not embedded (e.g., too short, empty after
URL stripping) should be marked as SKIPPED rather than FAILED. FAILED
should be reserved for actual errors during embedding.

Revision ID: 20260131_120000
Revises: 20260126_120000
Create Date: 2026-01-31

"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "20260131_120000"
down_revision = "20260126_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old constraint and add new one with SKIPPED
    op.drop_constraint("source_item_embed_status_check", "source_item", type_="check")
    op.create_check_constraint(
        "source_item_embed_status_check",
        "source_item",
        "embed_status IN ('RAW','QUEUED','STORED','FAILED','SKIPPED')",
    )


def downgrade() -> None:
    # Check for SKIPPED rows before attempting downgrade
    conn = op.get_bind()
    result = conn.execute(
        text("SELECT COUNT(*) FROM source_item WHERE embed_status = 'SKIPPED'")
    )
    skipped_count = result.scalar()

    if skipped_count > 0:
        raise RuntimeError(
            f"Cannot downgrade: {skipped_count} rows have embed_status='SKIPPED'. "
            "Run: UPDATE source_item SET embed_status = 'FAILED' WHERE embed_status = 'SKIPPED' "
            "before downgrading."
        )

    op.drop_constraint("source_item_embed_status_check", "source_item", type_="check")
    op.create_check_constraint(
        "source_item_embed_status_check",
        "source_item",
        "embed_status IN ('RAW','QUEUED','STORED','FAILED')",
    )
