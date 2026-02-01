"""Encrypt sensitive credentials at rest.

This migration:
1. Adds encrypted columns for passwords/tokens
2. Migrates existing plaintext data to encrypted format
3. Drops plaintext columns

Affected tables:
- email_accounts: password -> password_encrypted
- github_accounts: access_token -> access_token_encrypted, private_key -> private_key_encrypted
- calendar_accounts: caldav_password -> caldav_password_encrypted

Revision ID: 20260201_encrypt_credentials
Revises: 20260201_session_summary
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

# revision identifiers, used by Alembic.
revision: str = "20260201_encrypt_credentials"
down_revision: Union[str, None] = "20260201_session_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Import encryption functions (only available at runtime)
    from memory.common.db.models.secrets import encrypt_value

    # === EMAIL ACCOUNTS ===
    # Add encrypted column
    op.add_column(
        "email_accounts",
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=True),
    )

    # Migrate existing data
    connection = op.get_bind()
    session = Session(bind=connection)

    # Migrate email_accounts.password
    result = connection.execute(
        sa.text("SELECT id, password FROM email_accounts WHERE password IS NOT NULL")
    )
    for row in result:
        encrypted = encrypt_value(row.password)
        connection.execute(
            sa.text("UPDATE email_accounts SET password_encrypted = :enc WHERE id = :id"),
            {"enc": encrypted, "id": row.id},
        )

    # Drop old column
    op.drop_column("email_accounts", "password")

    # === GITHUB ACCOUNTS ===
    # Add encrypted columns
    op.add_column(
        "github_accounts",
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "github_accounts",
        sa.Column("private_key_encrypted", sa.LargeBinary(), nullable=True),
    )

    # Migrate existing data
    result = connection.execute(
        sa.text("SELECT id, access_token, private_key FROM github_accounts WHERE access_token IS NOT NULL OR private_key IS NOT NULL")
    )
    for row in result:
        updates = {}
        if row.access_token:
            updates["access_token_encrypted"] = encrypt_value(row.access_token)
        if row.private_key:
            updates["private_key_encrypted"] = encrypt_value(row.private_key)
        if updates:
            # Build dynamic update
            set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
            updates["id"] = row.id
            connection.execute(
                sa.text(f"UPDATE github_accounts SET {set_clauses} WHERE id = :id"),
                updates,
            )

    # Drop old columns
    op.drop_column("github_accounts", "access_token")
    op.drop_column("github_accounts", "private_key")

    # === CALENDAR ACCOUNTS ===
    # Add encrypted column
    op.add_column(
        "calendar_accounts",
        sa.Column("caldav_password_encrypted", sa.LargeBinary(), nullable=True),
    )

    # Migrate existing data
    result = connection.execute(
        sa.text("SELECT id, caldav_password FROM calendar_accounts WHERE caldav_password IS NOT NULL")
    )
    for row in result:
        encrypted = encrypt_value(row.caldav_password)
        connection.execute(
            sa.text("UPDATE calendar_accounts SET caldav_password_encrypted = :enc WHERE id = :id"),
            {"enc": encrypted, "id": row.id},
        )

    # Drop old column
    op.drop_column("calendar_accounts", "caldav_password")

    session.close()


def downgrade() -> None:
    # Import decryption functions (only available at runtime)
    from memory.common.db.models.secrets import decrypt_value

    connection = op.get_bind()

    # === CALENDAR ACCOUNTS ===
    op.add_column(
        "calendar_accounts",
        sa.Column("caldav_password", sa.Text(), nullable=True),
    )

    result = connection.execute(
        sa.text("SELECT id, caldav_password_encrypted FROM calendar_accounts WHERE caldav_password_encrypted IS NOT NULL")
    )
    for row in result:
        decrypted = decrypt_value(row.caldav_password_encrypted)
        connection.execute(
            sa.text("UPDATE calendar_accounts SET caldav_password = :dec WHERE id = :id"),
            {"dec": decrypted, "id": row.id},
        )

    op.drop_column("calendar_accounts", "caldav_password_encrypted")

    # === GITHUB ACCOUNTS ===
    op.add_column(
        "github_accounts",
        sa.Column("access_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "github_accounts",
        sa.Column("private_key", sa.Text(), nullable=True),
    )

    result = connection.execute(
        sa.text("SELECT id, access_token_encrypted, private_key_encrypted FROM github_accounts WHERE access_token_encrypted IS NOT NULL OR private_key_encrypted IS NOT NULL")
    )
    for row in result:
        updates = {}
        if row.access_token_encrypted:
            updates["access_token"] = decrypt_value(row.access_token_encrypted)
        if row.private_key_encrypted:
            updates["private_key"] = decrypt_value(row.private_key_encrypted)
        if updates:
            set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
            updates["id"] = row.id
            connection.execute(
                sa.text(f"UPDATE github_accounts SET {set_clauses} WHERE id = :id"),
                updates,
            )

    op.drop_column("github_accounts", "private_key_encrypted")
    op.drop_column("github_accounts", "access_token_encrypted")

    # === EMAIL ACCOUNTS ===
    op.add_column(
        "email_accounts",
        sa.Column("password", sa.Text(), nullable=True),
    )

    result = connection.execute(
        sa.text("SELECT id, password_encrypted FROM email_accounts WHERE password_encrypted IS NOT NULL")
    )
    for row in result:
        decrypted = decrypt_value(row.password_encrypted)
        connection.execute(
            sa.text("UPDATE email_accounts SET password = :dec WHERE id = :id"),
            {"dec": decrypted, "id": row.id},
        )

    op.drop_column("email_accounts", "password_encrypted")
