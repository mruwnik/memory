"""
SQLAdmin views for the knowledge base database models.
"""

import logging

from sqladmin import Admin, ModelView

from memory.common.db.models import (
    AgentObservation,
    ArticleFeed,
    BlogPost,
    Book,
    BookSection,
    Chunk,
    Comic,
    GithubAccount,
    GithubItem,
    GithubRepo,
    MCPServer,
    DiscordMessage,
    EmailAccount,
    EmailAttachment,
    ForumPost,
    MailMessage,
    MiscDoc,
    Note,
    Photo,
    ScheduledLLMCall,
    SourceItem,
    User,
    GoogleDoc,
    GoogleFolder,
    GoogleAccount,
    CalendarEvent,
    CalendarAccount,
    Meeting,
)
from memory.common.db.models.discord import DiscordChannel, DiscordServer, DiscordUser

logger = logging.getLogger(__name__)

DEFAULT_COLUMNS = (
    "modality",
    "embed_status",
    "inserted_at",
    "tags",
    "size",
    "mime_type",
    "filename",
    "content",
)


def source_columns(model: type[SourceItem], *columns: str):
    return [
        getattr(model, c)
        for c in ("id",) + columns + DEFAULT_COLUMNS
        if hasattr(model, c)
    ]


# Create admin views for all models
class SourceItemAdmin(ModelView, model=SourceItem):
    column_list = source_columns(SourceItem)
    column_searchable_list = [
        "modality",
        "filename",
        "embed_status",
    ]


class ChunkAdmin(ModelView, model=Chunk):
    column_list = ["id", "source_id", "embedding_model", "created_at"]
    column_sortable_list = ["created_at"]


class MailMessageAdmin(ModelView, model=MailMessage):
    column_list = source_columns(
        MailMessage,
        "subject",
        "sender",
        "recipients",
        "folder",
        "message_id",
        "tags",
        "embed_status",
        "inserted_at",
    )
    column_searchable_list = [
        "subject",
        "sender",
        "recipients",
        "folder",
        "message_id",
    ]


class EmailAttachmentAdmin(ModelView, model=EmailAttachment):
    column_list = source_columns(EmailAttachment, "filename", "mime_type", "size")
    column_searchable_list = [
        "filename",
        "mime_type",
        "id",
    ]


class BlogPostAdmin(ModelView, model=BlogPost):
    column_list = source_columns(
        BlogPost, "title", "author", "url", "published", "domain"
    )
    column_searchable_list = ["title", "author", "domain", "id", "url"]


class ForumPostAdmin(ModelView, model=ForumPost):
    column_list = source_columns(
        ForumPost,
        "title",
        "authors",
        "published_at",
        "url",
        "karma",
        "votes",
        "comments",
        "score",
    )
    column_searchable_list = ["title", "authors", "id"]


class PhotoAdmin(ModelView, model=Photo):
    column_list = source_columns(Photo, "exif_taken_at", "camera")


class ComicAdmin(ModelView, model=Comic):
    column_list = source_columns(Comic, "title", "author", "published", "volume")
    column_searchable_list = ["title", "author", "id"]


class BookSectionAdmin(ModelView, model=BookSection):
    column_list = source_columns(
        BookSection,
        "section_title",
        "section_number",
        "section_level",
        "start_page",
        "end_page",
    )
    column_searchable_list = ["section_title", "id"]


class MiscDocAdmin(ModelView, model=MiscDoc):
    column_list = source_columns(MiscDoc, "path")
    column_searchable_list = ["path", "id"]


class BookAdmin(ModelView, model=Book):
    column_list = [
        "id",
        "title",
        "author",
        "series",
        "series_number",
        "published",
    ]
    column_searchable_list = ["title", "author", "id"]


class DiscordMessageAdmin(ModelView, model=DiscordMessage):
    column_list = [
        "id",
        "content",
        "images",
        "sent_at",
    ]
    column_searchable_list = ["content", "id", "images"]
    column_sortable_list = ["sent_at"]


class MCPServerAdmin(ModelView, model=MCPServer):
    column_list = [
        "id",
        "mcp_server_url",
        "client_id",
        "state",
        "code_verifier",
        "access_token",
        "refresh_token",
        "token_expires_at",
        "available_tools",
        "created_at",
        "updated_at",
    ]
    column_searchable_list = [
        "mcp_server_url",
        "client_id",
        "state",
        "id",
    ]
    column_sortable_list = [
        "created_at",
        "updated_at",
        "mcp_server_url",
        "client_id",
        "state",
        "id",
    ]


class ArticleFeedAdmin(ModelView, model=ArticleFeed):
    column_list = [
        "id",
        "title",
        "description",
        "url",
        "tags",
        "active",
        "created_at",
        "updated_at",
    ]
    column_searchable_list = ["title", "url", "id"]


class EmailAccountAdmin(ModelView, model=EmailAccount):
    column_list = [
        "id",
        "name",
        "tags",
        "email_address",
        "username",
        "use_ssl",
        "folders",
        "active",
        "created_at",
        "updated_at",
    ]
    column_searchable_list = ["name", "email_address", "id"]


class AgentObservationAdmin(ModelView, model=AgentObservation):
    column_list = [
        "id",
        "content",
        "subject",
        "observation_type",
        "confidence",
        "evidence",
        "inserted_at",
    ]
    column_searchable_list = ["subject", "observation_type", "id"]
    column_default_sort = [("inserted_at", True)]
    column_sortable_list = ["inserted_at"]


class NoteAdmin(ModelView, model=Note):
    column_list = [
        "id",
        "subject",
        "content",
        "note_type",
        "confidence",
        "tags",
        "inserted_at",
    ]
    column_searchable_list = ["subject", "content", "id"]
    column_default_sort = [("inserted_at", True)]
    column_sortable_list = ["inserted_at"]


class UserAdmin(ModelView, model=User):
    column_list = [
        "id",
        "user_type",
        "email",
        "api_key",
        "name",
        "created_at",
        "discord_users",
    ]


class DiscordUserAdmin(ModelView, model=DiscordUser):
    column_list = [
        "id",
        "username",
        "display_name",
        "track_messages",
        "ignore_messages",
        "allowed_tools",
        "disallowed_tools",
        "summary",
        "created_at",
        "updated_at",
    ]


class DiscordServerAdmin(ModelView, model=DiscordServer):
    column_list = [
        "id",
        "name",
        "description",
        "member_count",
        "last_sync_at",
        "track_messages",
        "ignore_messages",
        "allowed_tools",
        "disallowed_tools",
        "summary",
        "created_at",
        "updated_at",
    ]


class DiscordChannelAdmin(ModelView, model=DiscordChannel):
    column_list = [
        "id",
        "name",
        "description",
        "member_count",
        "last_sync_at",
        "track_messages",
        "ignore_messages",
        "allowed_tools",
        "disallowed_tools",
        "summary",
        "created_at",
        "updated_at",
    ]


class ScheduledLLMCallAdmin(ModelView, model=ScheduledLLMCall):
    column_list = [
        "id",
        "user",
        "topic",
        "scheduled_time",
        "model",
        "status",
        "error_message",
        "response",
        "discord_channel",
        "discord_user",
        "executed_at",
        "created_at",
        "updated_at",
    ]
    column_sortable_list = ["executed_at", "scheduled_time", "created_at", "updated_at"]


class GithubAccountAdmin(ModelView, model=GithubAccount):
    column_list = [
        "id",
        "name",
        "auth_type",
        "active",
        "last_sync_at",
        "created_at",
        "updated_at",
    ]
    column_searchable_list = ["name", "id"]
    # Sensitive columns (access_token, private_key) are already excluded from column_list
    form_excluded_columns = ["repos", "access_token", "private_key"]


class GithubRepoAdmin(ModelView, model=GithubRepo):
    column_list = [
        "id",
        "account",
        "owner",
        "name",
        "track_issues",
        "track_prs",
        "track_comments",
        "track_project_fields",
        "labels_filter",
        "tags",
        "check_interval",
        "full_sync_interval",
        "active",
        "last_sync_at",
        "last_full_sync_at",
        "created_at",
    ]
    column_searchable_list = ["owner", "name", "id"]


class GithubItemAdmin(ModelView, model=GithubItem):
    column_list = source_columns(
        GithubItem,
        "kind",
        "repo_path",
        "number",
        "title",
        "state",
        "author",
        "labels",
        "github_updated_at",
        "project_status",
    )
    column_searchable_list = ["title", "repo_path", "author", "id", "number"]
    column_sortable_list = ["github_updated_at", "created_at"]


class GoogleDocAdmin(ModelView, model=GoogleDoc):
    column_list = source_columns(
        GoogleDoc,
        "title",
        "folder_path",
        "owner",
        "last_modified_by",
        "word_count",
        "content_hash",
    )
    column_searchable_list = ["title", "folder_path", "owner", "last_modified_by", "id"]
    column_sortable_list = ["google_modified_at", "created_at"]


class GoogleFolderAdmin(ModelView, model=GoogleFolder):
    column_list = source_columns(
        GoogleFolder, "folder_name", "folder_path", "account", "active"
    )
    column_searchable_list = ["folder_name", "folder_path", "id"]
    column_sortable_list = ["last_sync_at", "created_at"]


class GoogleAccountAdmin(ModelView, model=GoogleAccount):
    column_list = [
        "id",
        "name",
        "email",
        "active",
        "last_sync_at",
        "created_at",
        "updated_at",
    ]
    column_searchable_list = ["name", "email", "id"]
    column_sortable_list = ["last_sync_at", "created_at"]


class MeetingAdmin(ModelView, model=Meeting):
    column_list = source_columns(
        Meeting,
        "title",
        "meeting_date",
        "duration_minutes",
        "source_tool",
        "summary",
        "notes",
        "extraction_status",
        "attendee_ids",
        "task_ids",
        "calendar_event_id",
    )
    column_searchable_list = [
        "title",
        "meeting_date",
        "duration_minutes",
        "source_tool",
        "summary",
        "notes",
        "extraction_status",
        "attendee_ids",
        "task_ids",
        "calendar_event_id",
        "id",
    ]
    column_sortable_list = ["meeting_date", "duration_minutes", "created_at"]


class CalendarEventAdmin(ModelView, model=CalendarEvent):
    column_list = source_columns(
        CalendarEvent,
        "event_title",
        "start_time",
        "end_time",
        "location",
        "content",
        "tags",
    )
    column_searchable_list = [
        "event_title",
        "start_time",
        "end_time",
        "location",
        "content",
        "tags",
        "id",
    ]
    column_sortable_list = ["start_time", "end_time", "inserted_at"]


class CalendarAccountAdmin(ModelView, model=CalendarAccount):
    column_list = [
        "id",
        "name",
        "email",
        "active",
        "last_sync_at",
        "created_at",
        "updated_at",
    ]
    column_searchable_list = ["name", "email", "id"]
    column_sortable_list = ["last_sync_at", "created_at"]


def setup_admin(admin: Admin):
    """Add all admin views to the admin instance with OAuth protection."""
    admin.add_view(SourceItemAdmin)
    admin.add_view(AgentObservationAdmin)
    admin.add_view(NoteAdmin)
    admin.add_view(ChunkAdmin)
    admin.add_view(EmailAccountAdmin)
    admin.add_view(MailMessageAdmin)
    admin.add_view(EmailAttachmentAdmin)
    admin.add_view(BookAdmin)
    admin.add_view(BookSectionAdmin)
    admin.add_view(MiscDocAdmin)
    admin.add_view(ArticleFeedAdmin)
    admin.add_view(BlogPostAdmin)
    admin.add_view(ForumPostAdmin)
    admin.add_view(ComicAdmin)
    admin.add_view(PhotoAdmin)
    admin.add_view(DiscordMessageAdmin)
    admin.add_view(UserAdmin)
    admin.add_view(DiscordUserAdmin)
    admin.add_view(DiscordServerAdmin)
    admin.add_view(DiscordChannelAdmin)
    admin.add_view(MCPServerAdmin)
    admin.add_view(ScheduledLLMCallAdmin)
    admin.add_view(GithubAccountAdmin)
    admin.add_view(GithubRepoAdmin)
    admin.add_view(GithubItemAdmin)
    admin.add_view(GoogleDocAdmin)
    admin.add_view(GoogleFolderAdmin)
    admin.add_view(GoogleAccountAdmin)
    admin.add_view(MeetingAdmin)
    admin.add_view(CalendarEventAdmin)
    admin.add_view(CalendarAccountAdmin)
