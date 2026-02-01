"""Encrypt Google OAuth and account credentials at rest.

This migration:
1. Adds encrypted columns for OAuth client secrets and tokens
2. Migrates existing plaintext data to encrypted format
3. Drops plaintext columns

Affected tables:
- google_oauth_config: client_secret -> client_secret_encrypted
- google_accounts: access_token -> access_token_encrypted, refresh_token -> refresh_token_encrypted

Revision ID: 20260201_encrypt_google
Revises: 20260201_encrypt_credentials
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

# revision identifiers, used by Alembic.
revision: str = "20260201_encrypt_google"
down_revision: Union[str, None] = "20260201_encrypt_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Import encryption functions (only available at runtime)
    from memory.common.db.models.secrets import encrypt_value

    connection = op.get_bind()

    # === GOOGLE OAUTH CONFIG ===
    # Add encrypted column
    op.add_column(
        "google_oauth_config",
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=True),
    )

    # Migrate existing data
    result = connection.execute(
        sa.text("SELECT id, client_secret FROM google_oauth_config WHERE client_secret IS NOT NULL")
    )
    rows_to_encrypt = list(result)
    for row in rows_to_encrypt:
        encrypted = encrypt_value(row.client_secret)
        connection.execute(
            sa.text("UPDATE google_oauth_config SET client_secret_encrypted = :enc WHERE id = :id"),
            {"enc": encrypted, "id": row.id},
        )

    # Verify all rows were encrypted before dropping column
    unencrypted_count = connection.execute(
        sa.text("""
            SELECT COUNT(*) FROM google_oauth_config
            WHERE client_secret IS NOT NULL AND client_secret_encrypted IS NULL
        """)
    ).scalar()
    if unencrypted_count > 0:
        raise RuntimeError(
            f"Migration failed: {unencrypted_count} google_oauth_config rows still have "
            "unencrypted client_secret without encrypted equivalent. Aborting to prevent data loss."
        )

    # Drop old column (safe now that all data is migrated)
    op.drop_column("google_oauth_config", "client_secret")

    # === GOOGLE ACCOUNTS ===
    # Add encrypted columns
    op.add_column(
        "google_accounts",
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "google_accounts",
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
    )

    # Migrate existing data
    result = connection.execute(
        sa.text("SELECT id, access_token, refresh_token FROM google_accounts WHERE access_token IS NOT NULL OR refresh_token IS NOT NULL")
    )
    rows_to_encrypt = list(result)
    for row in rows_to_encrypt:
        updates = {}
        if row.access_token:
            updates["access_token_encrypted"] = encrypt_value(row.access_token)
        if row.refresh_token:
            updates["refresh_token_encrypted"] = encrypt_value(row.refresh_token)
        if updates:
            set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
            updates["id"] = row.id
            connection.execute(
                sa.text(f"UPDATE google_accounts SET {set_clauses} WHERE id = :id"),
                updates,
            )

    # Verify all rows were encrypted before dropping columns
    unencrypted_count = connection.execute(
        sa.text("""
            SELECT COUNT(*) FROM google_accounts
            WHERE (access_token IS NOT NULL AND access_token_encrypted IS NULL)
               OR (refresh_token IS NOT NULL AND refresh_token_encrypted IS NULL)
        """)
    ).scalar()
    if unencrypted_count > 0:
        raise RuntimeError(
            f"Migration failed: {unencrypted_count} google_accounts rows still have "
            "unencrypted tokens without encrypted equivalents. Aborting to prevent data loss."
        )

    # Drop old columns (safe now that all data is migrated)
    op.drop_column("google_accounts", "access_token")
    op.drop_column("google_accounts", "refresh_token")


def downgrade() -> None:
    # Import decryption functions (only available at runtime)
    from memory.common.db.models.secrets import decrypt_value

    connection = op.get_bind()

    # === GOOGLE ACCOUNTS ===
    op.add_column(
        "google_accounts",
        sa.Column("access_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "google_accounts",
        sa.Column("refresh_token", sa.Text(), nullable=True),
    )

    result = connection.execute(
        sa.text("SELECT id, access_token_encrypted, refresh_token_encrypted FROM google_accounts WHERE access_token_encrypted IS NOT NULL OR refresh_token_encrypted IS NOT NULL")
    )
    for row in result:
        updates = {}
        if row.access_token_encrypted:
            updates["access_token"] = decrypt_value(row.access_token_encrypted)
        if row.refresh_token_encrypted:
            updates["refresh_token"] = decrypt_value(row.refresh_token_encrypted)
        if updates:
            set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
            updates["id"] = row.id
            connection.execute(
                sa.text(f"UPDATE google_accounts SET {set_clauses} WHERE id = :id"),
                updates,
            )

    op.drop_column("google_accounts", "refresh_token_encrypted")
    op.drop_column("google_accounts", "access_token_encrypted")

    # === GOOGLE OAUTH CONFIG ===
    op.add_column(
        "google_oauth_config",
        sa.Column("client_secret", sa.Text(), nullable=True),
    )

    result = connection.execute(
        sa.text("SELECT id, client_secret_encrypted FROM google_oauth_config WHERE client_secret_encrypted IS NOT NULL")
    )
    for row in result:
        decrypted = decrypt_value(row.client_secret_encrypted)
        connection.execute(
            sa.text("UPDATE google_oauth_config SET client_secret = :dec WHERE id = :id"),
            {"dec": decrypted, "id": row.id},
        )

    op.drop_column("google_oauth_config", "client_secret_encrypted")
