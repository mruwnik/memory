"""Add Google Drive integration tables

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2025-12-29 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create google_oauth_config table (for storing OAuth credentials)
    op.create_table(
        "google_oauth_config",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column(
            "auth_uri",
            sa.Text(),
            server_default="https://accounts.google.com/o/oauth2/auth",
            nullable=False,
        ),
        sa.Column(
            "token_uri",
            sa.Text(),
            server_default="https://oauth2.googleapis.com/token",
            nullable=False,
        ),
        sa.Column(
            "redirect_uris", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "javascript_origins", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
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
        sa.UniqueConstraint("name"),
    )

    # Create google_accounts table
    op.create_table(
        "google_accounts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("email"),
    )
    op.create_index(
        "google_accounts_active_idx", "google_accounts", ["active", "last_sync_at"]
    )
    op.create_index("google_accounts_email_idx", "google_accounts", ["email"])

    # Create google_folders table
    op.create_table(
        "google_folders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("folder_id", sa.Text(), nullable=False),
        sa.Column("folder_name", sa.Text(), nullable=False),
        sa.Column("folder_path", sa.Text(), nullable=True),
        sa.Column("recursive", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "include_shared", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "mime_type_filter", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("tags", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("check_interval", sa.Integer(), server_default="60", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["account_id"], ["google_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "folder_id", name="unique_folder_per_account"
        ),
    )
    op.create_index(
        "google_folders_active_idx", "google_folders", ["active", "last_sync_at"]
    )

    # Create google_doc table (inherits from source_item)
    op.create_table(
        "google_doc",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("google_file_id", sa.Text(), nullable=False),
        sa.Column("google_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_mime_type", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.BigInteger(), nullable=True),
        sa.Column("folder_path", sa.Text(), nullable=True),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("last_modified_by", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["folder_id"], ["google_folders.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "google_doc_file_id_idx", "google_doc", ["google_file_id"], unique=True
    )
    op.create_index("google_doc_folder_idx", "google_doc", ["folder_id"])
    op.create_index("google_doc_modified_idx", "google_doc", ["google_modified_at"])
    op.create_index("google_doc_title_idx", "google_doc", ["title"])


def downgrade() -> None:
    # Drop google_doc table
    op.drop_index("google_doc_title_idx", table_name="google_doc")
    op.drop_index("google_doc_modified_idx", table_name="google_doc")
    op.drop_index("google_doc_folder_idx", table_name="google_doc")
    op.drop_index("google_doc_file_id_idx", table_name="google_doc")
    op.drop_table("google_doc")

    # Drop google_folders table
    op.drop_index("google_folders_active_idx", table_name="google_folders")
    op.drop_table("google_folders")

    # Drop google_accounts table
    op.drop_index("google_accounts_email_idx", table_name="google_accounts")
    op.drop_index("google_accounts_active_idx", table_name="google_accounts")
    op.drop_table("google_accounts")

    # Drop google_oauth_config table
    op.drop_table("google_oauth_config")
