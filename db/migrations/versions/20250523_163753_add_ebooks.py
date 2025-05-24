"""Add ebooks

Revision ID: fe570eab952a
Revises: b78b1fff9974
Create Date: 2025-05-23 16:37:53.354723

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fe570eab952a"
down_revision: Union[str, None] = "b78b1fff9974"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "book",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("isbn", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("published", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("edition", sa.Text(), nullable=True),
        sa.Column("series", sa.Text(), nullable=True),
        sa.Column("series_number", sa.Integer(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("isbn"),
    )
    op.create_index("book_author_idx", "book", ["author"], unique=False)
    op.create_index("book_isbn_idx", "book", ["isbn"], unique=False)
    op.create_index("book_title_idx", "book", ["title"], unique=False)
    op.create_table(
        "book_section",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("book_id", sa.BigInteger(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("section_number", sa.Integer(), nullable=True),
        sa.Column("section_level", sa.Integer(), nullable=True),
        sa.Column("start_page", sa.Integer(), nullable=True),
        sa.Column("end_page", sa.Integer(), nullable=True),
        sa.Column("parent_section_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["book_id"], ["book.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_section_id"],
            ["book_section.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("book_section_book_idx", "book_section", ["book_id"], unique=False)
    op.create_index(
        "book_section_level_idx",
        "book_section",
        ["section_level", "section_number"],
        unique=False,
    )
    op.create_index(
        "book_section_parent_idx", "book_section", ["parent_section_id"], unique=False
    )
    op.drop_table("book_doc")


def downgrade() -> None:
    op.create_table(
        "book_doc",
        sa.Column("id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("title", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("author", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("chapter", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "published",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["id"], ["source_item.id"], name="book_doc_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="book_doc_pkey"),
    )
    op.drop_index("book_section_parent_idx", table_name="book_section")
    op.drop_index("book_section_level_idx", table_name="book_section")
    op.drop_index("book_section_book_idx", table_name="book_section")
    op.drop_table("book_section")
    op.drop_index("book_title_idx", table_name="book")
    op.drop_index("book_isbn_idx", table_name="book")
    op.drop_index("book_author_idx", table_name="book")
    op.drop_table("book")
