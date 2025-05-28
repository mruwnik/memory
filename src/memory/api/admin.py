"""
SQLAdmin views for the knowledge base database models.
"""

from sqladmin import Admin, ModelView

from memory.common.db.models import (
    Chunk,
    SourceItem,
    MailMessage,
    EmailAttachment,
    Photo,
    Comic,
    Book,
    BookSection,
    BlogPost,
    MiscDoc,
    ArticleFeed,
    EmailAccount,
    ForumPost,
)


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
    ]


class BlogPostAdmin(ModelView, model=BlogPost):
    column_list = source_columns(
        BlogPost, "title", "author", "url", "published", "domain"
    )
    column_searchable_list = ["title", "author", "domain"]


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
    column_searchable_list = ["title", "authors"]


class PhotoAdmin(ModelView, model=Photo):
    column_list = source_columns(Photo, "exif_taken_at", "camera")


class ComicAdmin(ModelView, model=Comic):
    column_list = source_columns(Comic, "title", "author", "published", "volume")
    column_searchable_list = ["title", "author"]


class BookSectionAdmin(ModelView, model=BookSection):
    column_list = source_columns(
        BookSection,
        "section_title",
        "section_number",
        "section_level",
        "start_page",
        "end_page",
    )
    column_searchable_list = ["section_title"]


class MiscDocAdmin(ModelView, model=MiscDoc):
    column_list = source_columns(MiscDoc, "path")
    column_searchable_list = ["path"]


class BookAdmin(ModelView, model=Book):
    column_list = [
        "id",
        "title",
        "author",
        "series",
        "series_number",
        "published",
    ]
    column_searchable_list = ["title", "author"]


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
    column_searchable_list = ["title", "url"]


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
    column_searchable_list = ["name", "email_address"]


def setup_admin(admin: Admin):
    """Add all admin views to the admin instance."""
    admin.add_view(SourceItemAdmin)
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
