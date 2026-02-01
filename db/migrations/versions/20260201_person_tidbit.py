"""Refactor Person to thin identity record with PersonTidbit for content.

This migration:
1. Adds creator_id column to source_item table
2. Creates new people table (standalone, not FK to source_item)
3. Creates person_tidbits table (FK to source_item for inheritance)
4. Migrates data from old people → new people + tidbits
5. Updates FK references (discord_users, github_users, etc.)
6. Drops old people table and orphaned source_item rows

Revision ID: 20260201_person_tidbit
Revises: 20260131_150000
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260201_person_tidbit"
down_revision: Union[str, None] = "20260131_150000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Add creator_id to source_item
    op.add_column(
        "source_item",
        sa.Column(
            "creator_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("source_creator_idx", "source_item", ["creator_id"])

    # Step 2: Create new standalone people table
    op.create_table(
        "people_new",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("identifier", sa.Text(), unique=True, nullable=False, index=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "contact_info",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index("people_new_identifier_idx", "people_new", ["identifier"])
    op.create_index("people_new_display_name_idx", "people_new", ["display_name"])
    op.create_index(
        "people_new_aliases_idx", "people_new", ["aliases"], postgresql_using="gin"
    )
    op.create_index("people_new_user_idx", "people_new", ["user_id"])

    # Step 3: Create person_tidbits table (extends source_item)
    # Note: creator_id is inherited from source_item, not defined here
    op.create_table(
        "person_tidbits",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people_new.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tidbit_type", sa.Text(), nullable=False, server_default="note"),
    )
    op.create_index("person_tidbits_person_idx", "person_tidbits", ["person_id"])
    op.create_index("person_tidbits_type_idx", "person_tidbits", ["tidbit_type"])

    # Step 4: Migrate data from old people to new tables
    # First, copy identity data to people_new
    op.execute(
        """
        INSERT INTO people_new (id, identifier, display_name, aliases, contact_info, user_id, created_at, updated_at)
        SELECT p.id, p.identifier, p.display_name, p.aliases, p.contact_info, p.user_id,
               si.inserted_at, si.inserted_at
        FROM people p
        JOIN source_item si ON p.id = si.id
        """
    )

    # Create tidbits for people that have content
    # Note: creator_id is on source_item, not person_tidbits
    op.execute(
        """
        INSERT INTO person_tidbits (id, person_id, tidbit_type)
        SELECT p.id, p.id, 'note'
        FROM people p
        JOIN source_item si ON p.id = si.id
        WHERE si.content IS NOT NULL AND si.content != ''
        """
    )

    # Update polymorphic type for existing person source_items that have content
    op.execute(
        """
        UPDATE source_item
        SET type = 'person_tidbit'
        WHERE id IN (
            SELECT p.id FROM people p
            JOIN source_item si ON p.id = si.id
            WHERE si.content IS NOT NULL AND si.content != ''
        )
        """
    )

    # Step 5: Update FK references to point to people_new
    # 5a: discord_users.person_id
    op.drop_constraint("discord_users_person_id_fkey", "discord_users", type_="foreignkey")
    op.create_foreign_key(
        "discord_users_person_id_fkey",
        "discord_users",
        "people_new",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 5b: github_users.person_id
    op.drop_constraint("github_users_person_id_fkey", "github_users", type_="foreignkey")
    op.create_foreign_key(
        "github_users_person_id_fkey",
        "github_users",
        "people_new",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 5c: source_item_people.person_id
    op.drop_constraint(
        "source_item_people_person_id_fkey", "source_item_people", type_="foreignkey"
    )
    op.create_foreign_key(
        "source_item_people_person_id_fkey",
        "source_item_people",
        "people_new",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 5d: poll_responses.person_id
    op.drop_constraint("poll_responses_person_id_fkey", "poll_responses", type_="foreignkey")
    op.create_foreign_key(
        "poll_responses_person_id_fkey",
        "poll_responses",
        "people_new",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Step 5e: project_collaborators.person_id (will be dropped in teams migration,
    # but we need to temporarily update FK to allow dropping old people table)
    op.drop_constraint(
        "project_collaborators_person_id_fkey", "project_collaborators", type_="foreignkey"
    )
    op.create_foreign_key(
        "project_collaborators_person_id_fkey",
        "project_collaborators",
        "people_new",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Step 6: Drop old people table
    # Index names from complete_schema: ix_people_identifier (unique), person_aliases_idx,
    # person_display_name_idx, person_identifier_idx
    op.drop_index("person_aliases_idx", table_name="people")
    op.drop_index("person_display_name_idx", table_name="people")
    op.drop_index("person_identifier_idx", table_name="people")
    op.drop_index("ix_people_identifier", table_name="people")
    op.drop_table("people")

    # Step 7: Rename people_new to people
    op.rename_table("people_new", "people")
    op.execute("ALTER INDEX people_new_identifier_idx RENAME TO person_identifier_idx")
    op.execute("ALTER INDEX people_new_display_name_idx RENAME TO person_display_name_idx")
    op.execute("ALTER INDEX people_new_aliases_idx RENAME TO person_aliases_idx")
    op.execute("ALTER INDEX people_new_user_idx RENAME TO person_user_idx")

    # Step 8: Update FK references to point to renamed table
    # Need to recreate FKs with correct table name after rename
    op.drop_constraint("discord_users_person_id_fkey", "discord_users", type_="foreignkey")
    op.create_foreign_key(
        "discord_users_person_id_fkey",
        "discord_users",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("github_users_person_id_fkey", "github_users", type_="foreignkey")
    op.create_foreign_key(
        "github_users_person_id_fkey",
        "github_users",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "source_item_people_person_id_fkey", "source_item_people", type_="foreignkey"
    )
    op.create_foreign_key(
        "source_item_people_person_id_fkey",
        "source_item_people",
        "people",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("poll_responses_person_id_fkey", "poll_responses", type_="foreignkey")
    op.create_foreign_key(
        "poll_responses_person_id_fkey",
        "poll_responses",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Update person_tidbits FK to point to renamed table
    op.drop_constraint("person_tidbits_person_id_fkey", "person_tidbits", type_="foreignkey")
    op.create_foreign_key(
        "person_tidbits_person_id_fkey",
        "person_tidbits",
        "people",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Update project_collaborators FK to point to renamed table
    op.drop_constraint(
        "project_collaborators_person_id_fkey", "project_collaborators", type_="foreignkey"
    )
    op.create_foreign_key(
        "project_collaborators_person_id_fkey",
        "project_collaborators",
        "people",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Step 9: Clean up orphaned source_item rows (people without content)
    # These were person records that had no content - we don't need them as source_items anymore
    op.execute(
        """
        DELETE FROM source_item
        WHERE type = 'person'
        AND id NOT IN (SELECT id FROM person_tidbits)
        """
    )


def downgrade() -> None:
    # Recreate old people table structure (inherits from source_item)
    op.create_table(
        "people_old",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("identifier", sa.Text(), unique=True, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "contact_info",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Migrate data back: create source_items for each person
    # Note: This is lossy - we lose creator_id and tidbit separation
    op.execute(
        """
        INSERT INTO source_item (id, modality, sha256, tags, type, content, sensitivity)
        SELECT p.id, 'person',
               decode(md5('person:' || p.identifier), 'hex'),
               COALESCE(
                   (SELECT si.tags FROM source_item si
                    JOIN person_tidbits pt ON pt.id = si.id
                    WHERE pt.person_id = p.id LIMIT 1),
                   '{}'::text[]
               ),
               'person',
               (SELECT si.content FROM source_item si
                JOIN person_tidbits pt ON pt.id = si.id
                WHERE pt.person_id = p.id LIMIT 1),
               'basic'
        FROM people p
        WHERE p.id NOT IN (SELECT id FROM source_item)
        """
    )

    # Copy to old people structure
    op.execute(
        """
        INSERT INTO people_old (id, identifier, display_name, aliases, contact_info, user_id)
        SELECT id, identifier, display_name, aliases, contact_info, user_id
        FROM people
        """
    )

    # Update FK references back to people_old
    op.drop_constraint("discord_users_person_id_fkey", "discord_users", type_="foreignkey")
    op.create_foreign_key(
        "fk_discord_users_person_id",  # Restore original name
        "discord_users",
        "people_old",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("github_users_person_id_fkey", "github_users", type_="foreignkey")
    op.create_foreign_key(
        "github_users_person_id_fkey",
        "github_users",
        "people_old",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "source_item_people_person_id_fkey", "source_item_people", type_="foreignkey"
    )
    op.create_foreign_key(
        "source_item_people_person_id_fkey",
        "source_item_people",
        "people_old",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("poll_responses_person_id_fkey", "poll_responses", type_="foreignkey")
    op.create_foreign_key(
        "poll_responses_person_id_fkey",
        "poll_responses",
        "people_old",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "project_collaborators_person_id_fkey", "project_collaborators", type_="foreignkey"
    )
    op.create_foreign_key(
        "project_collaborators_person_id_fkey",
        "project_collaborators",
        "people_old",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Drop person_tidbits and new people table
    op.drop_table("person_tidbits")
    op.drop_table("people")

    # Rename old back to people
    op.rename_table("people_old", "people")

    # Recreate indexes
    op.create_index("ix_people_identifier", "people", ["identifier"], unique=True)
    op.create_index("person_identifier_idx", "people", ["identifier"])
    op.create_index("person_display_name_idx", "people", ["display_name"])
    op.create_index("person_aliases_idx", "people", ["aliases"], postgresql_using="gin")

    # Update FK references to use people
    op.drop_constraint("fk_discord_users_person_id", "discord_users", type_="foreignkey")
    op.create_foreign_key(
        "fk_discord_users_person_id",  # Restore original name
        "discord_users",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("github_users_person_id_fkey", "github_users", type_="foreignkey")
    op.create_foreign_key(
        "github_users_person_id_fkey",
        "github_users",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "source_item_people_person_id_fkey", "source_item_people", type_="foreignkey"
    )
    op.create_foreign_key(
        "source_item_people_person_id_fkey",
        "source_item_people",
        "people",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("poll_responses_person_id_fkey", "poll_responses", type_="foreignkey")
    op.create_foreign_key(
        "poll_responses_person_id_fkey",
        "poll_responses",
        "people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "project_collaborators_person_id_fkey", "project_collaborators", type_="foreignkey"
    )
    op.create_foreign_key(
        "project_collaborators_person_id_fkey",
        "project_collaborators",
        "people",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Remove creator_id from source_item
    op.drop_index("source_creator_idx", table_name="source_item")
    op.drop_column("source_item", "creator_id")
