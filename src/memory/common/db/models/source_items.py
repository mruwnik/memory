"""
Database models for the knowledge base system.
"""

import pathlib
import textwrap
from datetime import datetime
from typing import Any, Sequence, cast

from PIL import Image
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from memory.common import settings
import memory.common.extract as extract
import memory.common.summarizer as summarizer
import memory.common.formatters.observation as observation

from memory.common.db.models.source_item import (
    SourceItem,
    Chunk,
    clean_filename,
    merge_metadata,
    chunk_mixed,
)


class MailMessage(SourceItem):
    __tablename__ = "mail_message"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    message_id = Column(Text, unique=True)
    subject = Column(Text)
    sender = Column(Text)
    recipients = Column(ARRAY(Text))
    sent_at = Column(DateTime(timezone=True))
    folder = Column(Text)
    tsv = Column(TSVECTOR)

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "email"
        super().__init__(**kwargs)

    attachments = relationship(
        "EmailAttachment",
        back_populates="mail_message",
        foreign_keys="EmailAttachment.mail_message_id",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {
        "polymorphic_identity": "mail_message",
    }

    @property
    def attachments_path(self) -> pathlib.Path:
        clean_sender = clean_filename(cast(str, self.sender))
        clean_folder = clean_filename(cast(str | None, self.folder) or "INBOX")
        return pathlib.Path(settings.EMAIL_STORAGE_DIR) / clean_sender / clean_folder

    def safe_filename(self, filename: str) -> pathlib.Path:
        suffix = pathlib.Path(filename).suffix
        name = clean_filename(filename.removesuffix(suffix)) + suffix
        path = self.attachments_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def as_payload(self) -> dict:
        return {
            **super().as_payload(),
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "folder": self.folder,
            "tags": self.tags + [self.sender] + self.recipients,
            "date": (self.sent_at and self.sent_at.isoformat() or None),  # type: ignore
        }

    @property
    def parsed_content(self):
        from memory.parsers.email import parse_email_message

        return parse_email_message(cast(str, self.content), cast(str, self.message_id))

    @property
    def body(self) -> str:
        return self.parsed_content["body"]

    @property
    def display_contents(self) -> str | None:
        content = self.parsed_content
        return textwrap.dedent(
            """
            Subject: {subject}
            From: {sender}
            To: {recipients}
            Date: {date}
            Body: 
            {body}
            """
        ).format(
            subject=content.get("subject", ""),
            sender=content.get("from", ""),
            recipients=content.get("to", ""),
            date=content.get("date", ""),
            body=content.get("body", ""),
        )

    # Add indexes
    __table_args__ = (
        Index("mail_sent_idx", "sent_at"),
        Index("mail_recipients_idx", "recipients", postgresql_using="gin"),
        Index("mail_tsv_idx", "tsv", postgresql_using="gin"),
    )


class EmailAttachment(SourceItem):
    __tablename__ = "email_attachment"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    mail_message_id = Column(
        BigInteger, ForeignKey("mail_message.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    mail_message = relationship(
        "MailMessage", back_populates="attachments", foreign_keys=[mail_message_id]
    )

    __mapper_args__ = {
        "polymorphic_identity": "email_attachment",
    }

    def as_payload(self) -> dict:
        return {
            **super().as_payload(),
            "filename": self.filename,
            "content_type": self.mime_type,
            "size": self.size,
            "created_at": (self.created_at and self.created_at.isoformat() or None),  # type: ignore
            "mail_message_id": self.mail_message_id,
        }

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[Chunk]:
        if cast(str | None, self.filename):
            contents = pathlib.Path(cast(str, self.filename)).read_bytes()
        else:
            contents = cast(str, self.content)

        chunks = extract.extract_data_chunks(cast(str, self.mime_type), contents)
        return [self._make_chunk(c, metadata) for c in chunks]

    # Add indexes
    __table_args__ = (Index("email_attachment_message_idx", "mail_message_id"),)


class ChatMessage(SourceItem):
    __tablename__ = "chat_message"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    platform = Column(Text)
    channel_id = Column(Text)
    author = Column(Text)
    sent_at = Column(DateTime(timezone=True))

    __mapper_args__ = {
        "polymorphic_identity": "chat_message",
    }

    # Add index
    __table_args__ = (Index("chat_channel_idx", "platform", "channel_id"),)


class GitCommit(SourceItem):
    __tablename__ = "git_commit"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    repo_path = Column(Text)
    commit_sha = Column(Text, unique=True)
    author_name = Column(Text)
    author_email = Column(Text)
    author_date = Column(DateTime(timezone=True))
    diff_summary = Column(Text)
    files_changed = Column(ARRAY(Text))

    __mapper_args__ = {
        "polymorphic_identity": "git_commit",
    }

    # Add indexes
    __table_args__ = (
        Index("git_files_idx", "files_changed", postgresql_using="gin"),
        Index("git_date_idx", "author_date"),
    )


class Photo(SourceItem):
    __tablename__ = "photo"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    exif_taken_at = Column(DateTime(timezone=True))
    exif_lat = Column(Numeric(9, 6))
    exif_lon = Column(Numeric(9, 6))
    camera = Column(Text)

    __mapper_args__ = {
        "polymorphic_identity": "photo",
    }

    # Add index
    __table_args__ = (Index("photo_taken_idx", "exif_taken_at"),)


class Comic(SourceItem):
    __tablename__ = "comic"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    title = Column(Text)
    author = Column(Text, nullable=True)
    published = Column(DateTime(timezone=True), nullable=True)
    volume = Column(Text, nullable=True)
    issue = Column(Text, nullable=True)
    page = Column(Integer, nullable=True)
    url = Column(Text, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "comic",
    }

    __table_args__ = (Index("comic_author_idx", "author"),)

    def as_payload(self) -> dict:
        payload = {
            **super().as_payload(),
            "title": self.title,
            "author": self.author,
            "published": self.published,
            "volume": self.volume,
            "issue": self.issue,
            "page": self.page,
            "url": self.url,
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        image = Image.open(pathlib.Path(cast(str, self.filename)))
        description = f"{self.title} by {self.author}"
        return [extract.DataChunk(data=[image, description])]


class BookSection(SourceItem):
    """Individual sections/chapters of books"""

    __tablename__ = "book_section"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    book_id = Column(
        BigInteger, ForeignKey("book.id", ondelete="CASCADE"), nullable=False
    )

    section_title = Column(Text)
    section_number = Column(Integer)
    section_level = Column(Integer)  # 1=chapter, 2=section, 3=subsection
    start_page = Column(Integer)
    end_page = Column(Integer)

    # Parent-child relationships for nested sections
    parent_section_id = Column(BigInteger, ForeignKey("book_section.id"))

    book = relationship("Book", backref="sections")
    parent = relationship(
        "BookSection",
        remote_side=[id],
        backref="children",
        foreign_keys=[parent_section_id],
    )
    pages: list[str] = []

    __mapper_args__ = {"polymorphic_identity": "book_section"}
    __table_args__ = (
        Index("book_section_book_idx", "book_id"),
        Index("book_section_parent_idx", "parent_section_id"),
        Index("book_section_level_idx", "section_level", "section_number"),
    )

    def as_payload(self) -> dict:
        vals = {
            **super().as_payload(),
            "title": self.book.title,
            "author": self.book.author,
            "book_id": self.book_id,
            "section_title": self.section_title,
            "section_number": self.section_number,
            "section_level": self.section_level,
            "start_page": self.start_page,
            "end_page": self.end_page,
        }
        return {k: v for k, v in vals.items() if v}

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[Chunk]:
        content = cast(str, self.content.strip())
        if not content:
            return []

        if len([p for p in self.pages if p.strip()]) == 1:
            return [
                self._make_chunk(
                    extract.DataChunk(data=[content]), metadata | {"type": "page"}
                )
            ]

        summary, tags = summarizer.summarize(content)
        return [
            self._make_chunk(
                extract.DataChunk(data=[content]),
                merge_metadata(metadata, {"type": "section", "tags": tags}),
            ),
            self._make_chunk(
                extract.DataChunk(data=[summary]),
                merge_metadata(metadata, {"type": "summary", "tags": tags}),
            ),
        ]


class BlogPost(SourceItem):
    __tablename__ = "blog_post"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url = Column(Text, unique=True)
    title = Column(Text)
    author = Column(Text, nullable=True)
    published = Column(DateTime(timezone=True), nullable=True)

    # Additional metadata from webpage parsing
    description = Column(Text, nullable=True)  # Meta description or excerpt
    domain = Column(Text, nullable=True)  # Domain of the source website
    word_count = Column(Integer, nullable=True)  # Approximate word count
    images = Column(ARRAY(Text), nullable=True)  # List of image URLs

    # Store original metadata from parser
    webpage_metadata = Column(JSONB, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "blog_post",
    }

    __table_args__ = (
        Index("blog_post_author_idx", "author"),
        Index("blog_post_domain_idx", "domain"),
        Index("blog_post_published_idx", "published"),
        Index("blog_post_word_count_idx", "word_count"),
    )

    def as_payload(self) -> dict:
        published_date = cast(datetime | None, self.published)
        metadata = cast(dict | None, self.webpage_metadata) or {}

        payload = {
            **super().as_payload(),
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "published": published_date and published_date.isoformat(),
            "description": self.description,
            "domain": self.domain,
            "word_count": self.word_count,
            **metadata,
        }
        return {k: v for k, v in payload.items() if v}

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(cast(str, self.content), cast(list[str], self.images))


class ForumPost(SourceItem):
    __tablename__ = "forum_post"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url = Column(Text, unique=True)
    title = Column(Text)
    description = Column(Text, nullable=True)
    authors = Column(ARRAY(Text), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    modified_at = Column(DateTime(timezone=True), nullable=True)
    slug = Column(Text, nullable=True)
    karma = Column(Integer, nullable=True)
    votes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    words = Column(Integer, nullable=True)
    score = Column(Integer, nullable=True)
    images = Column(ARRAY(Text), nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "forum_post",
    }

    __table_args__ = (
        Index("forum_post_url_idx", "url"),
        Index("forum_post_slug_idx", "slug"),
        Index("forum_post_title_idx", "title"),
    )

    def as_payload(self) -> dict:
        return {
            **super().as_payload(),
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "authors": self.authors,
            "published_at": self.published_at,
            "slug": self.slug,
            "karma": self.karma,
            "votes": self.votes,
            "score": self.score,
            "comments": self.comments,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(cast(str, self.content), cast(list[str], self.images))


class MiscDoc(SourceItem):
    __tablename__ = "misc_doc"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    path = Column(Text)

    __mapper_args__ = {
        "polymorphic_identity": "misc_doc",
    }


class GithubItem(SourceItem):
    __tablename__ = "github_item"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    kind = Column(Text, nullable=False)
    repo_path = Column(Text, nullable=False)
    number = Column(Integer)
    parent_number = Column(Integer)
    commit_sha = Column(Text)
    state = Column(Text)
    title = Column(Text)
    labels = Column(ARRAY(Text))
    author = Column(Text)
    created_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))
    merged_at = Column(DateTime(timezone=True))
    diff_summary = Column(Text)

    payload = Column(JSONB)

    __mapper_args__ = {
        "polymorphic_identity": "github_item",
    }

    __table_args__ = (
        CheckConstraint("kind IN ('issue', 'pr', 'comment', 'project_card')"),
        Index("gh_repo_kind_idx", "repo_path", "kind"),
        Index("gh_issue_lookup_idx", "repo_path", "kind", "number"),
        Index("gh_labels_idx", "labels", postgresql_using="gin"),
    )


class AgentObservation(SourceItem):
    """
    Records observations made by AI agents about the user.
    This is the primary data model for the epistemic sparring partner.
    """

    __tablename__ = "agent_observation"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    session_id = Column(
        UUID(as_uuid=True)
    )  # Groups observations from same conversation
    observation_type = Column(
        Text, nullable=False
    )  # belief, preference, pattern, contradiction, behavior
    subject = Column(Text, nullable=False)  # What/who the observation is about
    confidence = Column(Numeric(3, 2), nullable=False, default=0.8)  # 0.0-1.0
    evidence = Column(JSONB)  # Supporting context, quotes, etc.
    agent_model = Column(Text, nullable=False)  # Which AI model made this observation

    # Relationships
    contradictions_as_first = relationship(
        "ObservationContradiction",
        foreign_keys="ObservationContradiction.observation_1_id",
        back_populates="observation_1",
        cascade="all, delete-orphan",
    )
    contradictions_as_second = relationship(
        "ObservationContradiction",
        foreign_keys="ObservationContradiction.observation_2_id",
        back_populates="observation_2",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {
        "polymorphic_identity": "agent_observation",
    }

    __table_args__ = (
        Index("agent_obs_session_idx", "session_id"),
        Index("agent_obs_type_idx", "observation_type"),
        Index("agent_obs_subject_idx", "subject"),
        Index("agent_obs_confidence_idx", "confidence"),
        Index("agent_obs_model_idx", "agent_model"),
    )

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "observation"
        super().__init__(**kwargs)

    def as_payload(self) -> dict:
        payload = {
            **super().as_payload(),
            "observation_type": self.observation_type,
            "subject": self.subject,
            "confidence": float(cast(Any, self.confidence)),
            "evidence": self.evidence,
            "agent_model": self.agent_model,
        }
        if self.session_id is not None:
            payload["session_id"] = str(self.session_id)
        return payload

    @property
    def all_contradictions(self):
        """Get all contradictions involving this observation."""
        return self.contradictions_as_first + self.contradictions_as_second

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[extract.DataChunk]:
        """
        Generate multiple chunks for different embedding dimensions.
        Each chunk goes to a different Qdrant collection for specialized search.
        """
        chunks = []

        # 1. Semantic chunk - standard content representation
        semantic_text = observation.generate_semantic_text(
            cast(str, self.subject),
            cast(str, self.observation_type),
            cast(str, self.content),
            cast(observation.Evidence, self.evidence),
        )
        chunks.append(
            extract.DataChunk(
                data=[semantic_text],
                metadata=merge_metadata(metadata, {"embedding_type": "semantic"}),
                collection_name="semantic",
            )
        )

        # 2. Temporal chunk - time-aware representation
        temporal_text = observation.generate_temporal_text(
            cast(str, self.subject),
            cast(str, self.content),
            cast(float, self.confidence),
            cast(datetime, self.inserted_at),
        )
        chunks.append(
            extract.DataChunk(
                data=[temporal_text],
                metadata=merge_metadata(metadata, {"embedding_type": "temporal"}),
                collection_name="temporal",
            )
        )

        # TODO: Add more embedding dimensions here:
        # 3. Epistemic chunk - belief structure focused
        # epistemic_text = self._generate_epistemic_text()
        # chunks.append(extract.DataChunk(
        #     data=[epistemic_text],
        #     metadata={**base_metadata, "embedding_type": "epistemic"},
        #     collection_name="observations_epistemic"
        # ))
        #
        # 4. Emotional chunk - emotional context focused
        # emotional_text = self._generate_emotional_text()
        # chunks.append(extract.DataChunk(
        #     data=[emotional_text],
        #     metadata={**base_metadata, "embedding_type": "emotional"},
        #     collection_name="observations_emotional"
        # ))
        #
        # 5. Relational chunk - connection patterns focused
        # relational_text = self._generate_relational_text()
        # chunks.append(extract.DataChunk(
        #     data=[relational_text],
        #     metadata={**base_metadata, "embedding_type": "relational"},
        #     collection_name="observations_relational"
        # ))

        return chunks
