"""
Database models for the knowledge base system.
"""

from typing import cast

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class Book(Base):
    """Book-level metadata table"""

    __tablename__ = "book"

    id = Column(BigInteger, primary_key=True)
    isbn = Column(Text, unique=True)
    title = Column(Text, nullable=False)
    author = Column(Text)
    publisher = Column(Text)
    published = Column(DateTime(timezone=True))
    language = Column(Text)
    edition = Column(Text)
    series = Column(Text)
    series_number = Column(Integer)
    total_pages = Column(Integer)
    file_path = Column(Text)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Metadata from ebook parser
    book_metadata = Column(JSONB, name="metadata")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("book_isbn_idx", "isbn"),
        Index("book_author_idx", "author"),
        Index("book_title_idx", "title"),
    )

    def as_payload(self, sections: bool = False) -> dict:
        data = {
            "id": self.id,
            "isbn": self.isbn,
            "title": self.title,
            "author": self.author,
            "publisher": self.publisher,
            "published": self.published,
            "language": self.language,
            "edition": self.edition,
            "series": self.series,
            "series_number": self.series_number,
        } | (cast(dict, self.book_metadata) or {})
        if sections:
            data["sections"] = [section.as_payload() for section in self.sections]
        return data


class ArticleFeed(Base):
    __tablename__ = "article_feeds"

    id = Column(BigInteger, primary_key=True)
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text)
    description = Column(Text)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    check_interval = Column(
        Integer, nullable=False, server_default="60", doc="Minutes between checks"
    )
    last_checked_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Add indexes
    __table_args__ = (
        Index("article_feeds_active_idx", "active", "last_checked_at"),
        Index("article_feeds_tags_idx", "tags", postgresql_using="gin"),
    )


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False)
    email_address = Column(Text, nullable=False, unique=True)
    imap_server = Column(Text, nullable=False)
    imap_port = Column(Integer, nullable=False, server_default="993")
    username = Column(Text, nullable=False)
    password = Column(Text, nullable=False)
    use_ssl = Column(Boolean, nullable=False, server_default="true")
    folders = Column(ARRAY(Text), nullable=False, server_default="{}")
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    last_sync_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Add indexes
    __table_args__ = (
        Index("email_accounts_address_idx", "email_address", unique=True),
        Index("email_accounts_active_idx", "active", "last_sync_at"),
        Index("email_accounts_tags_idx", "tags", postgresql_using="gin"),
    )


class GithubAccount(Base):
    """GitHub authentication credentials for API access."""

    __tablename__ = "github_accounts"

    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False)  # Display name

    # Authentication - support both PAT and GitHub App
    auth_type = Column(Text, nullable=False)  # 'pat' or 'app'

    # For Personal Access Token auth
    access_token = Column(Text, nullable=True)  # PAT

    # For GitHub App auth
    app_id = Column(BigInteger, nullable=True)
    installation_id = Column(BigInteger, nullable=True)
    private_key = Column(Text, nullable=True)  # PEM key

    # Status
    active = Column(Boolean, nullable=False, server_default="true")
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship to repos
    repos = relationship(
        "GithubRepo", back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("auth_type IN ('pat', 'app')"),
        Index("github_accounts_active_idx", "active", "last_sync_at"),
    )


class GithubRepo(Base):
    """Tracked GitHub repository configuration."""

    __tablename__ = "github_repos"

    id = Column(BigInteger, primary_key=True)
    account_id = Column(
        BigInteger, ForeignKey("github_accounts.id", ondelete="CASCADE"), nullable=False
    )

    # Repository identification
    owner = Column(Text, nullable=False)  # org or user
    name = Column(Text, nullable=False)  # repo name

    # What to track
    track_issues = Column(Boolean, nullable=False, server_default="true")
    track_prs = Column(Boolean, nullable=False, server_default="true")
    track_comments = Column(Boolean, nullable=False, server_default="true")
    track_project_fields = Column(Boolean, nullable=False, server_default="false")

    # Filtering
    labels_filter = Column(
        ARRAY(Text), nullable=False, server_default="{}"
    )  # Empty = all labels
    state_filter = Column(Text, nullable=True)  # 'open', 'closed', or None for all

    # Tags to apply to all items from this repo
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Sync configuration
    check_interval = Column(Integer, nullable=False, server_default="60")  # Minutes
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    # Full sync interval for catching project field changes (minutes, 0 = disabled)
    full_sync_interval = Column(Integer, nullable=False, server_default="1440")  # Daily
    last_full_sync_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    account = relationship("GithubAccount", back_populates="repos")

    __table_args__ = (
        UniqueConstraint("account_id", "owner", "name", name="unique_repo_per_account"),
        Index("github_repos_active_idx", "active", "last_sync_at"),
        Index("github_repos_owner_name_idx", "owner", "name"),
    )

    @property
    def repo_path(self) -> str:
        return f"{self.owner}/{self.name}"


class GoogleOAuthConfig(Base):
    """OAuth client configuration for Google APIs (from credentials JSON)."""

    __tablename__ = "google_oauth_config"

    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False, unique=True, default="default")
    client_id = Column(Text, nullable=False)
    client_secret = Column(Text, nullable=False)
    project_id = Column(Text, nullable=True)
    auth_uri = Column(
        Text, nullable=False, server_default="https://accounts.google.com/o/oauth2/auth"
    )
    token_uri = Column(
        Text, nullable=False, server_default="https://oauth2.googleapis.com/token"
    )
    redirect_uris = Column(ARRAY(Text), nullable=False, server_default="{}")
    javascript_origins = Column(ARRAY(Text), nullable=False, server_default="{}")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @classmethod
    def from_json(cls, json_data: dict, name: str = "default") -> "GoogleOAuthConfig":
        """Create from Google credentials JSON file content."""
        # Handle both "web" and "installed" credential types
        creds = json_data.get("web") or json_data.get("installed") or json_data
        return cls(
            name=name,
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            project_id=creds.get("project_id"),
            auth_uri=creds.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
            token_uri=creds.get("token_uri", "https://oauth2.googleapis.com/token"),
            redirect_uris=creds.get("redirect_uris", []),
            javascript_origins=creds.get("javascript_origins", []),
        )

    def to_client_config(self) -> dict:
        """Convert to format expected by google_auth_oauthlib.flow.Flow."""
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "project_id": self.project_id,
                "auth_uri": self.auth_uri,
                "token_uri": self.token_uri,
                "redirect_uris": list(self.redirect_uris or []),
            }
        }


class GoogleAccount(Base):
    """Google authentication credentials for Drive API access."""

    __tablename__ = "google_accounts"

    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False)  # Display name
    email = Column(Text, nullable=False, unique=True)  # Google account email

    # OAuth2 tokens
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Scopes granted
    scopes = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Status
    active = Column(Boolean, nullable=False, server_default="true")
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    sync_error = Column(Text, nullable=True)  # Last error message if any

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship to folders
    folders = relationship(
        "GoogleFolder", back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("google_accounts_active_idx", "active", "last_sync_at"),
        Index("google_accounts_email_idx", "email"),
    )


class GoogleFolder(Base):
    """Tracked Google Drive folder configuration."""

    __tablename__ = "google_folders"

    id = Column(BigInteger, primary_key=True)
    account_id = Column(
        BigInteger, ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False
    )

    # Folder identification
    folder_id = Column(Text, nullable=False)  # Google Drive folder ID
    folder_name = Column(Text, nullable=False)  # Display name
    folder_path = Column(Text, nullable=True)  # Full path for display

    # Sync options
    recursive = Column(Boolean, nullable=False, server_default="true")  # Include subfolders
    include_shared = Column(
        Boolean, nullable=False, server_default="false"
    )  # Include shared files

    # File type filters (empty = all text documents)
    mime_type_filter = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Tags to apply to all documents from this folder
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Sync configuration
    check_interval = Column(Integer, nullable=False, server_default="60")  # Minutes
    last_sync_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    account = relationship("GoogleAccount", back_populates="folders")

    __table_args__ = (
        UniqueConstraint("account_id", "folder_id", name="unique_folder_per_account"),
        Index("google_folders_active_idx", "active", "last_sync_at"),
    )
