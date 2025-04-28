"""Initial structure

Revision ID: a466a07360d5
Revises:
Create Date: 2025-04-27 17:15:37.487616

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a466a07360d5"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Create enum type for github_item with IF NOT EXISTS
    op.execute(
        "DO $$ BEGIN CREATE TYPE gh_item_kind AS ENUM ('issue','pr','comment','project_card'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )

    op.create_table(
        "email_accounts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email_address", sa.Text(), nullable=False),
        sa.Column("imap_server", sa.Text(), nullable=False),
        sa.Column("imap_port", sa.Integer(), server_default="993", nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column("use_ssl", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("folders", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_address"),
    )
    op.create_table(
        "rss_feeds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_table(
        "source_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("modality", sa.Text(), nullable=False),
        sa.Column("sha256", postgresql.BYTEA(), nullable=False),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("lang", sa.Text(), nullable=True),
        sa.Column("model_hash", sa.Text(), nullable=True),
        sa.Column(
            "vector_ids", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("embed_status", sa.Text(), server_default="RAW", nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_table(
        "blog_post",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("published", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_table(
        "book_doc",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("chapter", sa.Text(), nullable=True),
        sa.Column("published", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chat_message",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_raw", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "git_commit",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_path", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=True),
        sa.Column("author_email", sa.Text(), nullable=True),
        sa.Column("author_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("msg_raw", sa.Text(), nullable=True),
        sa.Column("diff_summary", sa.Text(), nullable=True),
        sa.Column("files_changed", sa.ARRAY(sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commit_sha"),
    )
    op.create_table(
        "mail_message",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("recipients", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_raw", sa.Text(), nullable=True),
        sa.Column("folder", sa.Text(), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_table(
        "email_attachment",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("mail_message_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("content", postgresql.BYTEA(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["mail_message_id"], ["mail_message.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_item.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "misc_doc",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "photo",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("exif_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exif_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("exif_lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("camera", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add github_item table
    op.create_table(
        "github_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("repo_path", sa.Text(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("parent_number", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body_raw", sa.Text(), nullable=True),
        sa.Column("labels", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diff_summary", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add constraint to github_item.kind
    op.create_check_constraint(
        "github_item_kind_check",
        "github_item",
        "kind IN ('issue', 'pr', 'comment', 'project_card')",
    )

    # Add missing constraint to source_item
    op.create_check_constraint(
        "source_item_embed_status_check",
        "source_item",
        "embed_status IN ('RAW','QUEUED','STORED','FAILED')",
    )

    # Create trigger function for vector_ids validation
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_vector_ids_not_empty()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.embed_status = 'STORED'
         AND (NEW.vector_ids IS NULL OR array_length(NEW.vector_ids,1) = 0) THEN
            RAISE EXCEPTION
              USING MESSAGE = 'vector_ids must not be empty when embed_status = STORED';
      END IF;
      RETURN NEW;
    END;
    $$;
    """)

    # Create trigger
    op.execute("""
    CREATE TRIGGER check_vector_ids
    BEFORE UPDATE ON source_item
    FOR EACH ROW EXECUTE FUNCTION trg_vector_ids_not_empty();
    """)

    # Create indexes for source_item
    op.create_index("source_modality_idx", "source_item", ["modality"])
    op.create_index("source_status_idx", "source_item", ["embed_status"])
    op.create_index("source_tags_idx", "source_item", ["tags"], postgresql_using="gin")

    # Create indexes for mail_message
    op.create_index("mail_sent_idx", "mail_message", ["sent_at"])
    op.create_index(
        "mail_recipients_idx", "mail_message", ["recipients"], postgresql_using="gin"
    )
    op.create_index("email_attachment_filename_idx", "email_attachment", ["filename"], unique=False)
    op.create_index("email_attachment_message_idx", "email_attachment", ["mail_message_id"], unique=False)
    op.create_index("mail_tsv_idx", "mail_message", ["tsv"], postgresql_using="gin")

    # Create index for chat_message
    op.create_index("chat_channel_idx", "chat_message", ["platform", "channel_id"])

    # Create indexes for git_commit
    op.create_index(
        "git_files_idx", "git_commit", ["files_changed"], postgresql_using="gin"
    )
    op.create_index("git_date_idx", "git_commit", ["author_date"])

    # Create index for photo
    op.create_index("photo_taken_idx", "photo", ["exif_taken_at"])

    # Create indexes for rss_feeds
    op.create_index("rss_feeds_active_idx", "rss_feeds", ["active", "last_checked_at"])
    op.create_index("rss_feeds_tags_idx", "rss_feeds", ["tags"], postgresql_using="gin")

    # Create indexes for email_accounts
    op.create_index(
        "email_accounts_address_idx", "email_accounts", ["email_address"], unique=True
    )
    op.create_index(
        "email_accounts_active_idx", "email_accounts", ["active", "last_sync_at"]
    )
    op.create_index(
        "email_accounts_tags_idx", "email_accounts", ["tags"], postgresql_using="gin"
    )

    # Create indexes for github_item
    op.create_index("gh_repo_kind_idx", "github_item", ["repo_path", "kind"])
    op.create_index(
        "gh_issue_lookup_idx", "github_item", ["repo_path", "kind", "number"]
    )
    op.create_index("gh_labels_idx", "github_item", ["labels"], postgresql_using="gin")

    # Create add_tags helper function
    op.execute("""
    CREATE OR REPLACE FUNCTION add_tags(p_source BIGINT, p_tags TEXT[])
    RETURNS VOID LANGUAGE SQL AS $$
    UPDATE source_item
       SET tags =
           (SELECT ARRAY(SELECT DISTINCT unnest(tags || p_tags)))
     WHERE id = p_source;
    $$;
    """)


def downgrade() -> None:
    # Drop indexes
    op.drop_index("gh_labels_idx", table_name="github_item")
    op.drop_index("gh_issue_lookup_idx", table_name="github_item")
    op.drop_index("gh_repo_kind_idx", table_name="github_item")
    op.drop_index("email_accounts_tags_idx", table_name="email_accounts")
    op.drop_index("email_accounts_active_idx", table_name="email_accounts")
    op.drop_index("email_accounts_address_idx", table_name="email_accounts")
    op.drop_index("rss_feeds_tags_idx", table_name="rss_feeds")
    op.drop_index("rss_feeds_active_idx", table_name="rss_feeds")
    op.drop_index("photo_taken_idx", table_name="photo")
    op.drop_index("git_date_idx", table_name="git_commit")
    op.drop_index("git_files_idx", table_name="git_commit")
    op.drop_index("chat_channel_idx", table_name="chat_message")
    op.drop_index("mail_tsv_idx", table_name="mail_message")
    op.drop_index("mail_recipients_idx", table_name="mail_message")
    op.drop_index("mail_sent_idx", table_name="mail_message")
    op.drop_index("email_attachment_message_idx", table_name="email_attachment")
    op.drop_index("email_attachment_filename_idx", table_name="email_attachment")
    op.drop_index("source_tags_idx", table_name="source_item")
    op.drop_index("source_status_idx", table_name="source_item")
    op.drop_index("source_modality_idx", table_name="source_item")

    # Drop tables
    op.drop_table("photo")
    op.drop_table("misc_doc")
    op.drop_table("git_commit")
    op.drop_table("chat_message")
    op.drop_table("book_doc")
    op.drop_table("blog_post")
    op.drop_table("github_item")
    op.drop_table("email_attachment")
    op.drop_table("mail_message")
    op.drop_table("source_item")
    op.drop_table("rss_feeds")
    op.drop_table("email_accounts")

    # Drop triggers and functions
    op.execute("DROP TRIGGER IF EXISTS check_vector_ids ON source_item")
    op.execute("DROP FUNCTION IF EXISTS trg_vector_ids_not_empty()")
    op.execute("DROP FUNCTION IF EXISTS add_tags(BIGINT, TEXT[])")

    # Drop enum type
    op.execute("DROP TYPE IF EXISTS gh_item_kind")
