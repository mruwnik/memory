"""Reusable SSH keys and Notes host trust; preserve existing encrypted user keys."""

import base64
import hashlib

from alembic import op
import sqlalchemy as sa

revision = "20261108_ssh_keys"
down_revision = "20261107_drop_slack_app_users"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ssh_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("private_key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ssh_keys_user_id", "ssh_keys", ["user_id"])
    op.add_column("users", sa.Column("default_ssh_key_id", sa.Integer()))
    op.create_foreign_key(
        "users_default_ssh_key_fk",
        "users",
        "ssh_keys",
        ["default_ssh_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    db = op.get_bind()
    rows = (
        db.execute(
            sa.text(
                "SELECT id, ssh_private_key_encrypted, ssh_public_key FROM users WHERE ssh_private_key_encrypted IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        public = row["ssh_public_key"] or ""
        fingerprint = None
        try:
            fingerprint = "SHA256:" + base64.b64encode(
                hashlib.sha256(
                    base64.b64decode(public.split()[1], validate=True)
                ).digest()
            ).decode().rstrip("=")
        except (ValueError, IndexError):
            pass
        key_id = db.execute(
            sa.text(
                "INSERT INTO ssh_keys (user_id, name, private_key_encrypted, public_key, fingerprint) VALUES (:uid, 'Migrated user key', :private, :public, :fingerprint) RETURNING id"
            ),
            {
                "uid": row["id"],
                "private": row["ssh_private_key_encrypted"],
                "public": public,
                "fingerprint": fingerprint,
            },
        ).scalar_one()
        db.execute(
            sa.text("UPDATE users SET default_ssh_key_id = :key WHERE id = :uid"),
            {"key": key_id, "uid": row["id"]},
        )
    op.drop_column("users", "ssh_private_key_encrypted")
    op.drop_column("users", "ssh_public_key")
    op.create_table(
        "notes_ssh_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ssh_key_id",
            sa.Integer(),
            sa.ForeignKey("ssh_keys.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("remote_url", sa.Text(), nullable=False),
        sa.Column("host_keys", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="notes_ssh_singleton"),
    )


def downgrade():
    op.add_column("users", sa.Column("ssh_public_key", sa.String()))
    op.add_column("users", sa.Column("ssh_private_key_encrypted", sa.LargeBinary()))
    op.execute(
        "UPDATE users SET ssh_public_key = k.public_key, ssh_private_key_encrypted = k.private_key_encrypted FROM ssh_keys k WHERE k.id = users.default_ssh_key_id"
    )
    op.drop_table("notes_ssh_config")
    op.drop_constraint("users_default_ssh_key_fk", "users", type_="foreignkey")
    op.drop_column("users", "default_ssh_key_id")
    op.drop_table("ssh_keys")
