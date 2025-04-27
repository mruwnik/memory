"""
Database models for the knowledge base system.
"""
from sqlalchemy import (
    Column, ForeignKey, Integer, BigInteger, Text, DateTime, Boolean, Float,
    ARRAY, func
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


class ChatMessage(Base):
    __tablename__ = 'chat_message'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    platform = Column(Text)
    channel_id = Column(Text)
    author = Column(Text)
    sent_at = Column(DateTime(timezone=True))
    body_raw = Column(Text)


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


class Photo(Base):
    __tablename__ = 'photo'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    file_path = Column(Text)
    exif_taken_at = Column(DateTime(timezone=True))
    exif_lat = Column(Float)
    exif_lon = Column(Float)
    camera_make = Column(Text)
    camera_model = Column(Text)


class BookDoc(Base):
    __tablename__ = 'book_doc'
    
    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey('source_item.id', ondelete='CASCADE'), nullable=False)
    title = Column(Text)
    author = Column(Text)
    chapter = Column(Text)
    published = Column(DateTime)


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