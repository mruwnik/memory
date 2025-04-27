"""
Database models for the knowledge base system.
"""
from sqlalchemy import (
    Column, ForeignKey, Integer, BigInteger, Text, DateTime, Boolean,
    ARRAY, func, Numeric, CheckConstraint, Index
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TSVECTOR
from sqlalchemy.ext.declarative import declarative_base


Base = declarative_base()


class SourceItem(Base):
    __tablename__ = 'source_item'
    
    id = Column(BigInteger, primary_key=True)
    modality = Column(Text, nullable=False)
    sha256 = Column(BYTEA, nullable=False, unique=True)
    inserted_at = Column(DateTime(timezone=True), server_default=func.now())
    tags = Column(ARRAY(Text), nullable=False, server_default='{}')
    lang = Column(Text)
    model_hash = Column(Text)
    vector_ids = Column(ARRAY(Text), nullable=False, server_default='{}')
    embed_status = Column(Text, nullable=False, server_default='RAW')
    byte_length = Column(Integer)
    mime_type = Column(Text)
    
    # Add table-level constraint and indexes
    __table_args__ = (
        CheckConstraint("embed_status IN ('RAW','QUEUED','STORED','FAILED')"),
        Index('source_modality_idx', 'modality'),
        Index('source_status_idx', 'embed_status'),
        Index('source_tags_idx', 'tags', postgresql_using='gin'),
    )


class MailMessage(Base):
    __tablename__ = 'mail_message'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    message_id = Column(Text, unique=True)
    subject = Column(Text)
    sender = Column(Text)
    recipients = Column(ARRAY(Text))
    sent_at = Column(DateTime(timezone=True))
    body_raw = Column(Text)
    attachments = Column(JSONB)
    tsv = Column(TSVECTOR)
    
    # Add indexes
    __table_args__ = (
        Index('mail_sent_idx', 'sent_at'),
        Index('mail_recipients_idx', 'recipients', postgresql_using='gin'),
        Index('mail_tsv_idx', 'tsv', postgresql_using='gin'),
    )


class ChatMessage(Base):
    __tablename__ = 'chat_message'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    platform = Column(Text)
    channel_id = Column(Text)
    author = Column(Text)
    sent_at = Column(DateTime(timezone=True))
    body_raw = Column(Text)
    
    # Add index
    __table_args__ = (
        Index('chat_channel_idx', 'platform', 'channel_id'),
    )


class GitCommit(Base):
    __tablename__ = 'git_commit'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    repo_path = Column(Text)
    commit_sha = Column(Text, unique=True)
    author_name = Column(Text)
    author_email = Column(Text)
    author_date = Column(DateTime(timezone=True))
    msg_raw = Column(Text)
    diff_summary = Column(Text)
    files_changed = Column(ARRAY(Text))
    
    # Add indexes
    __table_args__ = (
        Index('git_files_idx', 'files_changed', postgresql_using='gin'),
        Index('git_date_idx', 'author_date'),
    )


class Photo(Base):
    __tablename__ = 'photo'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    file_path = Column(Text)
    exif_taken_at = Column(DateTime(timezone=True))
    exif_lat = Column(Numeric(9, 6))
    exif_lon = Column(Numeric(9, 6))
    camera = Column(Text)
    
    # Add index
    __table_args__ = (
        Index('photo_taken_idx', 'exif_taken_at'),
    )


class BookDoc(Base):
    __tablename__ = 'book_doc'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    title = Column(Text)
    author = Column(Text)
    chapter = Column(Text)
    published = Column(DateTime(timezone=True))


class BlogPost(Base):
    __tablename__ = 'blog_post'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    url = Column(Text, unique=True)
    title = Column(Text)
    published = Column(DateTime(timezone=True))


class MiscDoc(Base):
    __tablename__ = 'misc_doc'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    path = Column(Text)
    mime_type = Column(Text)


class RssFeed(Base):
    __tablename__ = 'rss_feeds'
    
    id = Column(BigInteger, primary_key=True)
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text)
    description = Column(Text)
    tags = Column(ARRAY(Text), nullable=False, server_default='{}')
    last_checked_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, server_default='true')
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Add indexes
    __table_args__ = (
        Index('rss_feeds_active_idx', 'active', 'last_checked_at'),
        Index('rss_feeds_tags_idx', 'tags', postgresql_using='gin'),
    )


class EmailAccount(Base):
    __tablename__ = 'email_accounts'
    
    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False)
    email_address = Column(Text, nullable=False, unique=True)
    imap_server = Column(Text, nullable=False)
    imap_port = Column(Integer, nullable=False, server_default='993')
    username = Column(Text, nullable=False)
    password = Column(Text, nullable=False)
    use_ssl = Column(Boolean, nullable=False, server_default='true')
    folders = Column(ARRAY(Text), nullable=False, server_default='{}')
    tags = Column(ARRAY(Text), nullable=False, server_default='{}')
    last_sync_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, server_default='true')
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Add indexes
    __table_args__ = (
        Index('email_accounts_address_idx', 'email_address', unique=True),
        Index('email_accounts_active_idx', 'active', 'last_sync_at'),
        Index('email_accounts_tags_idx', 'tags', postgresql_using='gin'),
    )


class GithubItem(Base):
    __tablename__ = 'github_item'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    
    kind = Column(Text, nullable=False)
    repo_path = Column(Text, nullable=False)
    number = Column(Integer)
    parent_number = Column(Integer)
    commit_sha = Column(Text)
    state = Column(Text)
    title = Column(Text)
    body_raw = Column(Text)
    labels = Column(ARRAY(Text))
    author = Column(Text)
    created_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))
    merged_at = Column(DateTime(timezone=True))
    diff_summary = Column(Text)
    
    payload = Column(JSONB)
    
    __table_args__ = (
        CheckConstraint("kind IN ('issue', 'pr', 'comment', 'project_card')"),
        Index('gh_repo_kind_idx', 'repo_path', 'kind'),
        Index('gh_issue_lookup_idx', 'repo_path', 'kind', 'number'),
        Index('gh_labels_idx', 'labels', postgresql_using='gin'),
    ) 