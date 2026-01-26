"""Add access control v2: Projects and hierarchical access.

This migration implements project-based RBAC where:
- GithubMilestone (github_milestones table) is the central project entity
- Access is Person-based via project_collaborators junction table
- User -> Person -> project_collaborators -> Project
- Superadmins are users with admin scope (checked via has_admin_scope())
- Data sources inherit project/sensitivity to their items at query time

Tables created:
- github_users: GitHub user accounts linked to Persons
- project_collaborators: Junction table linking Persons to projects with roles
- source_item_people: Junction table linking SourceItems to Persons
- access_logs: Audit logging for access events
- source_item_access_view: View for BM25 query-time access resolution

Columns added:
- projects.parent_id (hierarchical projects)
- discord_channels.project_id, .sensitivity
- discord_servers.project_id, .sensitivity, .config_version
- slack_channels.project_id, .sensitivity
- slack_workspaces.project_id, .sensitivity, .config_version
- email_accounts.project_id, .sensitivity, .config_version
- google_folders.project_id, .sensitivity, .config_version
- calendar_accounts.project_id, .sensitivity, .config_version
- article_feeds.project_id, .sensitivity, .config_version
- source_item.project_id, .sensitivity

Revision ID: 20260124_120000
Revises: 20260123_120000
Create Date: 2026-01-24 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260124_120000"
down_revision: Union[str, None] = "20260123_120000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add parent_id to github_milestones for hierarchical organization
    op.add_column("github_milestones", sa.Column("parent_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_github_milestones_parent", "github_milestones", "github_milestones",
        ["parent_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint("ck_milestone_not_self_parent", "github_milestones", "id != parent_id")

    # Create github_users table
    op.create_table(
        "github_users",
        sa.Column("id", sa.BigInteger(), nullable=False),  # GitHub user ID
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("person_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("username", name="unique_github_username"),
    )
    op.create_index("github_users_username_idx", "github_users", ["username"])
    op.create_index("github_users_person_idx", "github_users", ["person_id"])

    # Create project_collaborators junction table (references renamed projects table)
    op.create_table(
        "project_collaborators",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="contributor"),
        sa.PrimaryKeyConstraint("project_id", "person_id"),
        sa.ForeignKeyConstraint(["project_id"], ["github_milestones.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('contributor', 'manager', 'admin')", name="valid_collaborator_role"),
    )
    op.create_index("project_collaborators_project_idx", "project_collaborators", ["project_id"])
    op.create_index("project_collaborators_person_idx", "project_collaborators", ["person_id"])

    # Create access_logs table
    op.create_table(
        "access_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("item_id", sa.BigInteger(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_access_logs_user_time", "access_logs", ["user_id", "timestamp"])
    op.create_index("idx_access_logs_time", "access_logs", ["timestamp"])
    op.execute(
        "CREATE INDEX idx_access_logs_item ON access_logs (item_id) WHERE item_id IS NOT NULL"
    )

    # Add project_id and sensitivity to discord_channels
    op.add_column(
        "discord_channels",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "discord_channels",
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"),
    )
    op.create_foreign_key(
        "fk_discord_channels_project",
        "discord_channels",
        "github_milestones",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "valid_discord_channel_sensitivity",
        "discord_channels",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
    )
    op.create_index("discord_channels_project_idx", "discord_channels", ["project_id"])

    # Add project_id, sensitivity, config_version to discord_servers
    op.add_column("discord_servers", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.add_column("discord_servers", sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"))
    op.add_column("discord_servers", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_discord_servers_project", "discord_servers", "github_milestones",
        ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "valid_discord_server_sensitivity", "discord_servers",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
    )
    op.create_index("discord_servers_project_idx", "discord_servers", ["project_id"])

    # Add project_id and sensitivity to slack_channels
    op.add_column(
        "slack_channels",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "slack_channels",
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"),
    )
    op.create_foreign_key(
        "fk_slack_channels_project",
        "slack_channels",
        "github_milestones",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "valid_slack_channel_sensitivity",
        "slack_channels",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
    )
    op.create_index("slack_channels_project_idx", "slack_channels", ["project_id"])

    # Add project_id, sensitivity, config_version to slack_workspaces
    op.add_column("slack_workspaces", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.add_column("slack_workspaces", sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"))
    op.add_column("slack_workspaces", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_slack_workspaces_project", "slack_workspaces", "github_milestones",
        ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "valid_slack_workspace_sensitivity", "slack_workspaces",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
    )
    op.create_index("slack_workspaces_project_idx", "slack_workspaces", ["project_id"])

    # Add project_id, sensitivity, config_version to email_accounts
    op.add_column("email_accounts", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.add_column("email_accounts", sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"))
    op.add_column("email_accounts", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_email_accounts_project", "email_accounts", "github_milestones",
        ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "valid_email_account_sensitivity", "email_accounts",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
    )
    op.create_index("email_accounts_project_idx", "email_accounts", ["project_id"])

    # Add project_id, sensitivity, config_version to google_folders
    op.add_column("google_folders", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.add_column("google_folders", sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"))
    op.add_column("google_folders", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_google_folders_project", "google_folders", "github_milestones",
        ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "valid_google_folder_sensitivity", "google_folders",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
    )
    op.create_index("google_folders_project_idx", "google_folders", ["project_id"])

    # Add project_id, sensitivity, config_version to calendar_accounts
    op.add_column("calendar_accounts", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.add_column("calendar_accounts", sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"))
    op.add_column("calendar_accounts", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_calendar_accounts_project", "calendar_accounts", "github_milestones",
        ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "valid_calendar_account_sensitivity", "calendar_accounts",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
    )
    op.create_index("calendar_accounts_project_idx", "calendar_accounts", ["project_id"])

    # Add project_id, sensitivity, config_version to article_feeds (default public for blogs)
    op.add_column("article_feeds", sa.Column("project_id", sa.BigInteger(), nullable=True))
    op.add_column("article_feeds", sa.Column("sensitivity", sa.String(20), nullable=False, server_default="public"))
    op.add_column("article_feeds", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_article_feeds_project", "article_feeds", "github_milestones",
        ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "valid_article_feed_sensitivity", "article_feeds",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
    )
    op.create_index("article_feeds_project_idx", "article_feeds", ["project_id"])

    # Add config_version to slack_channels and discord_channels
    op.add_column("slack_channels", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("discord_channels", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))

    # Add feed_id to blog_post for linking to article_feeds
    op.add_column("blog_post", sa.Column("feed_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_blog_post_feed", "blog_post", "article_feeds",
        ["feed_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("blog_post_feed_idx", "blog_post", ["feed_id"])

    # Add project_id and sensitivity to source_item
    op.add_column(
        "source_item",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "source_item",
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic"),
    )
    op.create_foreign_key(
        "fk_source_item_project",
        "source_item",
        "github_milestones",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "valid_sensitivity_level",
        "source_item",
        "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
    )
    op.create_index("source_project_idx", "source_item", ["project_id"])
    op.create_index("source_sensitivity_idx", "source_item", ["sensitivity"])

    # Create source_item_people junction table for person associations
    op.create_table(
        "source_item_people",
        sa.Column("source_item_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("source_item_id", "person_id"),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
    )
    op.create_index("source_item_people_source_idx", "source_item_people", ["source_item_id"])
    op.create_index("source_item_people_person_idx", "source_item_people", ["person_id"])

    # Migrate existing meeting_attendees data to source_item_people
    # Since Meeting.id = SourceItem.id (joined table inheritance), we can copy directly
    # Note: For very large meeting_attendees tables (millions of rows), consider using
    # batched inserts instead. For typical deployments, this single INSERT is fine.
    # If meeting_attendees is empty, this INSERT is a no-op (gracefully handled).
    op.execute("""
        INSERT INTO source_item_people (source_item_id, person_id)
        SELECT meeting_id, person_id FROM meeting_attendees
        ON CONFLICT DO NOTHING
    """)

    # Drop meeting_attendees table (replaced by source_item_people)
    op.drop_table("meeting_attendees")

    # Create view for BM25 access control resolution
    # Note: The CASE expression for resolved_sensitivity must match Python class defaults:
    # - blog_post, book_section, comic, forum_post: default_sensitivity = "public"
    # - All other types: default_sensitivity = "basic"
    op.execute("""
        CREATE VIEW source_item_access_view AS
        SELECT
            si.id,
            si.type,
            si.project_id AS item_project_id,
            si.sensitivity AS item_sensitivity,
            COALESCE(si.project_id,
                CASE si.type
                    WHEN 'mail_message' THEN ea.project_id
                    WHEN 'slack_message' THEN COALESCE(sc.project_id, sw.project_id)
                    WHEN 'discord_message' THEN COALESCE(dc.project_id, ds.project_id)
                    WHEN 'calendar_event' THEN ca.project_id
                    WHEN 'google_doc' THEN gf.project_id
                    WHEN 'blog_post' THEN af.project_id
                END
            ) AS resolved_project_id,
            COALESCE(si.sensitivity,
                CASE si.type
                    WHEN 'mail_message' THEN ea.sensitivity
                    WHEN 'slack_message' THEN COALESCE(sc.sensitivity, sw.sensitivity)
                    WHEN 'discord_message' THEN COALESCE(dc.sensitivity, ds.sensitivity)
                    WHEN 'calendar_event' THEN ca.sensitivity
                    WHEN 'google_doc' THEN gf.sensitivity
                    WHEN 'blog_post' THEN COALESCE(af.sensitivity, 'public')
                    WHEN 'book_section' THEN 'public'
                    WHEN 'comic' THEN 'public'
                    WHEN 'forum_post' THEN 'public'
                END,
                'basic'
            ) AS resolved_sensitivity
        FROM source_item si
        LEFT JOIN mail_message mm ON si.id = mm.id AND si.type = 'mail_message'
        LEFT JOIN email_accounts ea ON mm.email_account_id = ea.id
        LEFT JOIN slack_message sm ON si.id = sm.id AND si.type = 'slack_message'
        LEFT JOIN slack_channels sc ON sm.channel_id = sc.id
        LEFT JOIN slack_workspaces sw ON sc.workspace_id = sw.id
        LEFT JOIN discord_message dm ON si.id = dm.id AND si.type = 'discord_message'
        LEFT JOIN discord_channels dc ON dm.channel_id = dc.id
        LEFT JOIN discord_servers ds ON dc.server_id = ds.id
        LEFT JOIN calendar_event ce ON si.id = ce.id AND si.type = 'calendar_event'
        LEFT JOIN calendar_accounts ca ON ce.calendar_account_id = ca.id
        LEFT JOIN google_doc gd ON si.id = gd.id AND si.type = 'google_doc'
        LEFT JOIN google_folders gf ON gd.folder_id = gf.id
        LEFT JOIN blog_post bp ON si.id = bp.id AND si.type = 'blog_post'
        LEFT JOIN article_feeds af ON bp.feed_id = af.id
    """)


def downgrade() -> None:
    # WARNING: This downgrade has data loss implications.
    # Person associations for non-Meeting content types (emails, Slack messages, Google Docs,
    # Discord messages, etc.) created after this migration was applied will be LOST on downgrade.
    # Only Meeting attendee associations are preserved (migrated back to meeting_attendees).
    # This is acceptable because:
    # 1. Downgrades are rare and typically only used in development/testing
    # 2. The person associations can be re-created by re-syncing content

    # Drop the access view
    op.execute("DROP VIEW IF EXISTS source_item_access_view")

    # Recreate meeting_attendees table (was replaced by source_item_people)
    op.create_table(
        "meeting_attendees",
        sa.Column("meeting_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("meeting_id", "person_id"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meeting.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
    )

    # Migrate data back from source_item_people to meeting_attendees
    # Note: Only Meeting associations are preserved; other content types lose their person links
    op.execute("""
        INSERT INTO meeting_attendees (meeting_id, person_id)
        SELECT source_item_id, person_id FROM source_item_people
        WHERE source_item_id IN (SELECT id FROM meeting)
        ON CONFLICT DO NOTHING
    """)

    # Drop source_item_people junction table (indexes are dropped automatically with table)
    op.drop_table("source_item_people")

    # Remove indexes and columns from source_item
    op.drop_index("source_sensitivity_idx", table_name="source_item")
    op.drop_index("source_project_idx", table_name="source_item")
    op.drop_constraint("valid_sensitivity_level", "source_item", type_="check")
    op.drop_constraint("fk_source_item_project", "source_item", type_="foreignkey")
    op.drop_column("source_item", "sensitivity")
    op.drop_column("source_item", "project_id")

    # Remove config_version from slack_channels and discord_channels
    op.drop_column("slack_channels", "config_version")
    op.drop_column("discord_channels", "config_version")

    # Remove feed_id from blog_post
    op.drop_index("blog_post_feed_idx", table_name="blog_post")
    op.drop_constraint("fk_blog_post_feed", "blog_post", type_="foreignkey")
    op.drop_column("blog_post", "feed_id")

    # Remove from article_feeds
    op.drop_index("article_feeds_project_idx", table_name="article_feeds")
    op.drop_constraint("valid_article_feed_sensitivity", "article_feeds", type_="check")
    op.drop_constraint("fk_article_feeds_project", "article_feeds", type_="foreignkey")
    op.drop_column("article_feeds", "config_version")
    op.drop_column("article_feeds", "sensitivity")
    op.drop_column("article_feeds", "project_id")

    # Remove from calendar_accounts
    op.drop_index("calendar_accounts_project_idx", table_name="calendar_accounts")
    op.drop_constraint("valid_calendar_account_sensitivity", "calendar_accounts", type_="check")
    op.drop_constraint("fk_calendar_accounts_project", "calendar_accounts", type_="foreignkey")
    op.drop_column("calendar_accounts", "config_version")
    op.drop_column("calendar_accounts", "sensitivity")
    op.drop_column("calendar_accounts", "project_id")

    # Remove from google_folders
    op.drop_index("google_folders_project_idx", table_name="google_folders")
    op.drop_constraint("valid_google_folder_sensitivity", "google_folders", type_="check")
    op.drop_constraint("fk_google_folders_project", "google_folders", type_="foreignkey")
    op.drop_column("google_folders", "config_version")
    op.drop_column("google_folders", "sensitivity")
    op.drop_column("google_folders", "project_id")

    # Remove from email_accounts
    op.drop_index("email_accounts_project_idx", table_name="email_accounts")
    op.drop_constraint("valid_email_account_sensitivity", "email_accounts", type_="check")
    op.drop_constraint("fk_email_accounts_project", "email_accounts", type_="foreignkey")
    op.drop_column("email_accounts", "config_version")
    op.drop_column("email_accounts", "sensitivity")
    op.drop_column("email_accounts", "project_id")

    # Remove from slack_workspaces
    op.drop_index("slack_workspaces_project_idx", table_name="slack_workspaces")
    op.drop_constraint("valid_slack_workspace_sensitivity", "slack_workspaces", type_="check")
    op.drop_constraint("fk_slack_workspaces_project", "slack_workspaces", type_="foreignkey")
    op.drop_column("slack_workspaces", "config_version")
    op.drop_column("slack_workspaces", "sensitivity")
    op.drop_column("slack_workspaces", "project_id")

    # Remove from slack_channels
    op.drop_index("slack_channels_project_idx", table_name="slack_channels")
    op.drop_constraint("valid_slack_channel_sensitivity", "slack_channels", type_="check")
    op.drop_constraint("fk_slack_channels_project", "slack_channels", type_="foreignkey")
    op.drop_column("slack_channels", "sensitivity")
    op.drop_column("slack_channels", "project_id")

    # Remove from discord_servers
    op.drop_index("discord_servers_project_idx", table_name="discord_servers")
    op.drop_constraint("valid_discord_server_sensitivity", "discord_servers", type_="check")
    op.drop_constraint("fk_discord_servers_project", "discord_servers", type_="foreignkey")
    op.drop_column("discord_servers", "config_version")
    op.drop_column("discord_servers", "sensitivity")
    op.drop_column("discord_servers", "project_id")

    # Remove from discord_channels
    op.drop_index("discord_channels_project_idx", table_name="discord_channels")
    op.drop_constraint("valid_discord_channel_sensitivity", "discord_channels", type_="check")
    op.drop_constraint("fk_discord_channels_project", "discord_channels", type_="foreignkey")
    op.drop_column("discord_channels", "sensitivity")
    op.drop_column("discord_channels", "project_id")

    # Drop access_logs
    op.execute("DROP INDEX IF EXISTS idx_access_logs_item")
    op.drop_index("idx_access_logs_time", table_name="access_logs")
    op.drop_index("idx_access_logs_user_time", table_name="access_logs")
    op.drop_table("access_logs")

    # Drop project_collaborators
    op.drop_index("project_collaborators_person_idx", table_name="project_collaborators")
    op.drop_index("project_collaborators_project_idx", table_name="project_collaborators")
    op.drop_table("project_collaborators")

    # Drop github_users
    op.drop_index("github_users_person_idx", table_name="github_users")
    op.drop_index("github_users_username_idx", table_name="github_users")
    op.drop_table("github_users")

    # Remove parent_id from github_milestones
    op.drop_constraint("ck_milestone_not_self_parent", "github_milestones", type_="check")
    op.drop_constraint("fk_github_milestones_parent", "github_milestones", type_="foreignkey")
    op.drop_column("github_milestones", "parent_id")
