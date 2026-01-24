"""
Database models for the knowledge base system.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

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
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship, validates

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.people import Person
    from memory.common.db.models.source_items import BookSection, GithubItem, MailMessage
    from memory.common.db.models.users import User


class Book(Base):
    """Book-level metadata table"""

    __tablename__ = "book"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    sections: Mapped[list["BookSection"]] = relationship("BookSection", back_populates="book")
    isbn: Mapped[str | None] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    published: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(Text)
    edition: Mapped[str | None] = mapped_column(Text)
    series: Mapped[str | None] = mapped_column(Text)
    series_number: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    file_path: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Metadata from ebook parser
    book_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, name="metadata")

    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
        } | (self.book_metadata or {})
        if sections:
            data["sections"] = [section.as_payload() for section in self.sections]
        return data


class ArticleFeed(Base):
    __tablename__ = "article_feeds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    check_interval: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60", doc="Minutes between checks"
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Add indexes
    __table_args__ = (
        Index("article_feeds_active_idx", "active", "last_checked_at"),
        Index("article_feeds_tags_idx", "tags", postgresql_using="gin"),
    )


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email_address: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # Account type: 'imap' or 'gmail'
    account_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="imap")

    # IMAP fields (nullable for Gmail accounts)
    imap_server: Mapped[str | None] = mapped_column(Text, nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_ssl: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # SMTP fields (optional - inferred from IMAP if not set)
    smtp_server: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Gmail fields (nullable for IMAP accounts)
    google_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("google_accounts.id"), nullable=True
    )

    # Common fields
    folders: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")  # sync enabled
    send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id], backref="email_accounts")
    google_account: Mapped[GoogleAccount | None] = relationship("GoogleAccount", foreign_keys=[google_account_id])
    messages: Mapped[list[MailMessage]] = relationship(
        "MailMessage",
        back_populates="email_account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("account_type IN ('imap', 'gmail')"),
        Index("email_accounts_address_idx", "email_address", unique=True),
        Index("email_accounts_active_idx", "active", "last_sync_at"),
        Index("email_accounts_tags_idx", "tags", postgresql_using="gin"),
        Index("email_accounts_type_idx", "account_type"),
        Index("email_accounts_user_idx", "user_id"),
    )

    @validates("smtp_port")
    def validate_smtp_port(self, key: str, value: int | None) -> int | None:
        """Validate SMTP port is in valid range if set."""
        if value is not None:
            if not (1 <= value <= 65535):
                raise ValueError(f"SMTP port must be between 1 and 65535, got {value}")
        return value

    @validates("smtp_server")
    def validate_smtp_config(self, key: str, value: str | None) -> str | None:
        """Warn if smtp_port is set without smtp_server."""
        # Note: Can't easily enforce smtp_server when port is set due to
        # validation order uncertainty. The email_sender module handles
        # runtime validation of SMTP configuration.
        return value


class GithubAccount(Base):
    """GitHub authentication credentials for API access."""

    __tablename__ = "github_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # Display name

    # Authentication - support both PAT and GitHub App
    auth_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'pat' or 'app'

    # For Personal Access Token auth
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # PAT

    # For GitHub App auth
    app_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    private_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # PEM key

    # Status
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped[User] = relationship("User", foreign_keys=[user_id], backref="github_accounts")
    repos: Mapped[list[GithubRepo]] = relationship(
        "GithubRepo", back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("auth_type IN ('pat', 'app')"),
        Index("github_accounts_active_idx", "active", "last_sync_at"),
        Index("github_accounts_user_idx", "user_id"),
    )


class GithubRepo(Base):
    """Tracked GitHub repository configuration."""

    __tablename__ = "github_repos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("github_accounts.id", ondelete="CASCADE"), nullable=False
    )

    # Repository identification
    owner: Mapped[str] = mapped_column(Text, nullable=False)  # org or user
    name: Mapped[str] = mapped_column(Text, nullable=False)  # repo name

    # What to track
    track_issues: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    track_prs: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    track_comments: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    track_project_fields: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Filtering
    labels_filter: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )  # Empty = all labels
    state_filter: Mapped[str | None] = mapped_column(Text, nullable=True)  # 'open', 'closed', or None for all

    # Tags to apply to all items from this repo
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Sync configuration
    check_interval: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")  # Minutes
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Full sync interval for catching project field changes (minutes, 0 = disabled)
    full_sync_interval: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1440")  # Daily
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Status
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    account: Mapped[GithubAccount] = relationship("GithubAccount", back_populates="repos")

    __table_args__ = (
        UniqueConstraint("account_id", "owner", "name", name="unique_repo_per_account"),
        Index("github_repos_active_idx", "active", "last_sync_at"),
        Index("github_repos_owner_name_idx", "owner", "name"),
    )

    @property
    def repo_path(self) -> str:
        return f"{self.owner}/{self.name}"


class GithubMilestone(Base):
    """GitHub milestone for tracking progress toward goals."""

    __tablename__ = "github_milestones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("github_repos.id", ondelete="CASCADE"), nullable=False
    )

    # GitHub identifiers
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Data
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)  # 'open' or 'closed'
    due_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps from GitHub
    github_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Local timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    repo: Mapped[GithubRepo] = relationship("GithubRepo", backref=backref("milestones", passive_deletes=True))
    items: Mapped[list[GithubItem]] = relationship("GithubItem", back_populates="milestone_rel")
    collaborators: Mapped[list["Person"]] = relationship(
        "Person", secondary="project_collaborators", back_populates="projects"
    )

    __table_args__ = (
        UniqueConstraint("repo_id", "number", name="unique_milestone_per_repo"),
        Index("github_milestones_repo_idx", "repo_id"),
        Index("github_milestones_due_idx", "due_on"),
    )

    @hybrid_property
    def slug(self) -> str | None:
        """Project slug in format owner/repo:number."""
        if self.repo is None:
            return None
        return f"{self.repo.owner}/{self.repo.name}:{self.number}"


# Junction table for milestone/project collaborators
project_collaborators = Table(
    "project_collaborators",
    Base.metadata,
    Column("project_id", BigInteger, ForeignKey("github_milestones.id", ondelete="CASCADE"), primary_key=True),
    Column("person_id", BigInteger, ForeignKey("people.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(50), nullable=False, server_default="contributor"),
    CheckConstraint("role IN ('contributor', 'manager', 'admin')", name="valid_collaborator_role"),
    Index("project_collaborators_project_idx", "project_id"),
    Index("project_collaborators_person_idx", "person_id"),
)


class GithubUser(Base):
    """GitHub user account linked to a Person."""

    __tablename__ = "github_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub user ID
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Link to Person
    person_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )
    person: Mapped["Person | None"] = relationship("Person", back_populates="github_accounts")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("github_users_username_idx", "username"),
        Index("github_users_person_idx", "person_id"),
    )

    def __repr__(self) -> str:
        return f"<GithubUser(id={self.id}, username={self.username!r})>"


class GithubProject(Base):
    """GitHub Project (v2) for tracking work across repos."""

    __tablename__ = "github_projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("github_accounts.id", ondelete="CASCADE"), nullable=False
    )

    # GitHub identifiers
    node_id: Mapped[str] = mapped_column(Text, nullable=False)  # GraphQL node ID
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # Project number (shown in URL)

    # Owner info
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'organization' or 'user'
    owner_login: Mapped[str] = mapped_column(Text, nullable=False)  # org or user name

    # Project data
    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    readme: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    # Status
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Field definitions stored as JSONB
    # Format: [{"id": "...", "name": "Status", "data_type": "SINGLE_SELECT", "options": {...}}]
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")

    # Stats
    items_total_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Timestamps from GitHub
    github_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Local timestamps
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    account: Mapped[GithubAccount] = relationship("GithubAccount", backref=backref("projects", passive_deletes=True))

    __table_args__ = (
        UniqueConstraint(
            "account_id", "owner_login", "number", name="unique_project_per_account"
        ),
        Index("github_projects_owner_idx", "owner_login", "number"),
        Index("github_projects_title_idx", "title"),
    )

    @property
    def project_path(self) -> str:
        """Return the project path in the format 'owner/number'."""
        return f"{self.owner_login}/{self.number}"

    def as_payload(self) -> dict:
        """Serialize for API response."""
        return {
            "id": self.id,
            "account_id": self.account_id,
            "node_id": self.node_id,
            "number": self.number,
            "owner_type": self.owner_type,
            "owner_login": self.owner_login,
            "title": self.title,
            "short_description": self.short_description,
            "readme": self.readme,
            "url": self.url,
            "public": self.public,
            "closed": self.closed,
            "fields": self.fields,
            "items_total_count": self.items_total_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "github_created_at": (
                self.github_created_at.isoformat() if self.github_created_at else None
            ),
            "github_updated_at": (
                self.github_updated_at.isoformat() if self.github_updated_at else None
            ),
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
        }


class GithubTeam(Base):
    """GitHub Team within an organization."""

    __tablename__ = "github_teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("github_accounts.id", ondelete="CASCADE"), nullable=False
    )

    # GitHub identifiers
    node_id: Mapped[str] = mapped_column(Text, nullable=False)  # GraphQL node ID
    slug: Mapped[str] = mapped_column(Text, nullable=False)  # URL-safe team name
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # REST API ID

    # Team info
    name: Mapped[str] = mapped_column(Text, nullable=False)  # Display name
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy: Mapped[str] = mapped_column(Text, nullable=False)  # 'closed' or 'secret'
    permission: Mapped[str | None] = mapped_column(Text, nullable=True)  # 'pull', 'push', 'admin', 'maintain', 'triage'

    # Organization info
    org_login: Mapped[str] = mapped_column(Text, nullable=False)

    # Parent team (for nested teams)
    parent_team_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("github_teams.id"), nullable=True)

    # Member count (cached)
    members_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Timestamps
    github_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    account: Mapped[GithubAccount] = relationship("GithubAccount", backref=backref("teams", passive_deletes=True))
    parent_team: Mapped[GithubTeam | None] = relationship("GithubTeam", remote_side=[id], backref="child_teams")

    __table_args__ = (
        UniqueConstraint(
            "account_id", "org_login", "slug", name="unique_team_per_account"
        ),
        Index("github_teams_org_idx", "org_login"),
        Index("github_teams_slug_idx", "slug"),
    )

    @property
    def team_path(self) -> str:
        """Return the team path in the format 'org/slug'."""
        return f"{self.org_login}/{self.slug}"

    def as_payload(self) -> dict:
        """Serialize for API response."""
        return {
            "id": self.id,
            "account_id": self.account_id,
            "node_id": self.node_id,
            "github_id": self.github_id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "privacy": self.privacy,
            "permission": self.permission,
            "org_login": self.org_login,
            "parent_team_id": self.parent_team_id,
            "members_count": self.members_count,
            "url": f"https://github.com/orgs/{self.org_login}/teams/{self.slug}",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "github_created_at": (
                self.github_created_at.isoformat() if self.github_created_at else None
            ),
            "github_updated_at": (
                self.github_updated_at.isoformat() if self.github_updated_at else None
            ),
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
        }


class GoogleOAuthConfig(Base):
    """OAuth client configuration for Google APIs (from credentials JSON)."""

    __tablename__ = "google_oauth_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default="default")
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_uri: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="https://accounts.google.com/o/oauth2/auth"
    )
    token_uri: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="https://oauth2.googleapis.com/token"
    )
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    javascript_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @classmethod
    def from_json(cls, json_data: dict, name: str = "default") -> GoogleOAuthConfig:
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # Display name
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # Google account email

    # OAuth2 tokens
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Scopes granted
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Status
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # Last error message if any

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped[User] = relationship("User", foreign_keys=[user_id], backref="google_accounts")
    folders: Mapped[list[GoogleFolder]] = relationship(
        "GoogleFolder", back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("google_accounts_active_idx", "active", "last_sync_at"),
        Index("google_accounts_email_idx", "email"),
        Index("google_accounts_user_idx", "user_id"),
    )


class GoogleFolder(Base):
    """Tracked Google Drive folder configuration."""

    __tablename__ = "google_folders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False
    )

    # Folder identification
    folder_id: Mapped[str] = mapped_column(Text, nullable=False)  # Google Drive folder ID
    folder_name: Mapped[str] = mapped_column(Text, nullable=False)  # Display name
    folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # Full path for display

    # Sync options
    recursive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")  # Include subfolders
    include_shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )  # Include shared files

    # File type filters (empty = all text documents)
    mime_type_filter: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Excluded subfolder IDs (skip these when syncing recursively)
    exclude_folder_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Tags to apply to all documents from this folder
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Sync configuration
    check_interval: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")  # Minutes
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Status
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    account: Mapped[GoogleAccount] = relationship("GoogleAccount", back_populates="folders")

    __table_args__ = (
        UniqueConstraint("account_id", "folder_id", name="unique_folder_per_account"),
        Index("google_folders_active_idx", "active", "last_sync_at"),
    )


class CalendarAccount(Base):
    """Calendar source for syncing events (CalDAV, Google Calendar, etc.)."""

    __tablename__ = "calendar_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # Display name

    # Calendar type
    calendar_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'caldav', 'google'

    # For CalDAV (Radicale, etc.)
    caldav_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # CalDAV server URL
    caldav_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    caldav_password: Mapped[str | None] = mapped_column(Text, nullable=True)

    # For Google Calendar - link to existing GoogleAccount
    google_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("google_accounts.id", ondelete="SET NULL"), nullable=True
    )

    # Which calendars to sync (empty = all)
    calendar_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Tags to apply to all events from this account
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Sync configuration
    check_interval: Mapped[int] = mapped_column(Integer, nullable=False, server_default="15")  # Minutes
    sync_past_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")  # How far back
    sync_future_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")  # How far ahead
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    google_account: Mapped[GoogleAccount | None] = relationship("GoogleAccount", foreign_keys=[google_account_id])

    __table_args__ = (
        CheckConstraint("calendar_type IN ('caldav', 'google')"),
        Index("calendar_accounts_active_idx", "active", "last_sync_at"),
        Index("calendar_accounts_type_idx", "calendar_type"),
    )
