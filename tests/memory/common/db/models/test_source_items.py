from sqlalchemy.orm import Session
from unittest.mock import patch, Mock, PropertyMock
from typing import Any, cast
import pytest
from PIL import Image
from datetime import datetime
import uuid
from memory.common import settings, chunker, extract
from memory.common.db.models.sources import Book
from memory.common.db.models.source_items import (
    MailMessage,
    EmailAttachment,
    BookSection,
    BlogPost,
    AgentObservation,
    GitCommit,
    Note,
    Meeting,
    DiscordMessage,
    SlackMessage,
    Photo,
    Comic,
)
from memory.common.db.models.source_item import SourceItem, truncate_preview
from memory.common.db.models import Person
from tests.data.contents import SAMPLE_MARKDOWN


@pytest.fixture
def default_chunk_size():
    chunk_length = chunker.DEFAULT_CHUNK_TOKENS
    real_chunker = chunker.chunk_text

    def chunk_text(text: str, max_tokens: int = 0):
        max_tokens = max_tokens or chunk_length
        return real_chunker(text, max_tokens=max_tokens)

    def set_size(new_size: int):
        nonlocal chunk_length
        chunk_length = new_size

    with patch.object(chunker, "chunk_text", chunk_text):
        yield set_size


@pytest.mark.parametrize(
    "modality,expected_modality",
    [
        (None, "email"),  # Default case
        ("custom", "custom"),  # Override case
    ],
)
def test_mail_message_modality(modality, expected_modality):
    """Test MailMessage modality setting"""
    kwargs = {"sha256": b"test", "content": "test"}
    if modality is not None:
        kwargs["modality"] = modality

    mail_message = MailMessage(**kwargs)
    # The __init__ method should set the correct modality
    assert hasattr(mail_message, "modality")


@pytest.mark.parametrize(
    "sender,folder,expected_path",
    [
        ("user@example.com", "INBOX", "user_example_com/INBOX"),
        ("user+tag@example.com", "Sent Items", "user_tag_example_com/Sent_Items"),
        ("user@domain.co.uk", None, "user_domain_co_uk/INBOX"),
        ("user@domain.co.uk", "", "user_domain_co_uk/INBOX"),
    ],
)
def test_mail_message_attachments_path(sender, folder, expected_path):
    """Test MailMessage.attachments_path property"""
    mail_message = MailMessage(
        sha256=b"test", content="test", sender=sender, folder=folder
    )

    result = mail_message.attachments_path
    assert str(result) == f"{settings.FILE_STORAGE_DIR}/emails/{expected_path}"


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("document.pdf", "document.pdf"),
        ("file with spaces.txt", "file_with_spaces.txt"),
        ("file@#$%^&*().doc", "file.doc"),
        ("no-extension", "no_extension"),
        ("multiple.dots.in.name.txt", "multiple_dots_in_name.txt"),
    ],
)
def test_mail_message_safe_filename(tmp_path, filename, expected):
    """Test MailMessage.safe_filename method"""
    mail_message = MailMessage(
        sha256=b"test", content="test", sender="user@example.com", folder="INBOX"
    )

    expected = settings.FILE_STORAGE_DIR / f"emails/user_example_com/INBOX/{expected}"
    assert mail_message.safe_filename(filename) == expected


@pytest.mark.parametrize(
    "sent_at,expected_date",
    [
        (datetime(2023, 1, 1, 12, 0, 0), "2023-01-01T12:00:00"),
        (None, None),
    ],
)
def test_mail_message_as_payload(sent_at, expected_date):
    """Test MailMessage.as_payload method"""

    mail_message = MailMessage(
        sha256=b"test",
        content="test",
        message_id="<test@example.com>",
        subject="Test Subject",
        sender="sender@example.com",
        recipients=["recipient1@example.com", "recipient2@example.com"],
        folder="INBOX",
        sent_at=sent_at,
        tags=["tag1", "tag2"],
        size=1024,
    )
    # Manually set id for testing
    object.__setattr__(mail_message, "id", 123)
    mail_message.people = []

    payload = mail_message.as_payload()

    expected = {
        "source_id": 123,
        "size": 1024,
        "people": [],
        "project_id": None,
        "sensitivity": "basic",  # Default value from model
        "message_id": "<test@example.com>",
        "subject": "Test Subject",
        "sender": "sender@example.com",
        "recipients": ["recipient1@example.com", "recipient2@example.com"],
        "folder": "INBOX",
        "tags": [
            "tag1",
            "tag2",
            "sender@example.com",
            "recipient1@example.com",
            "recipient2@example.com",
        ],
        "date": expected_date,
    }
    assert payload == expected


def test_mail_message_parsed_content():
    """Test MailMessage.parsed_content property with actual email parsing"""
    # Use a simple email format that the parser can handle
    email_content = """From: sender@example.com
To: recipient@example.com
Subject: Test Subject

Test Body Content"""

    mail_message = MailMessage(
        sha256=b"test", content=email_content, message_id="<test@example.com>"
    )

    result = mail_message.parsed_content

    # Just test that it returns a dict-like object
    assert isinstance(result, dict)
    assert "body" in result


def test_mail_message_body_property():
    """Test MailMessage.body property with actual email parsing"""
    email_content = """From: sender@example.com
To: recipient@example.com
Subject: Test Subject

Test Body Content"""

    mail_message = MailMessage(
        sha256=b"test", content=email_content, message_id="<test@example.com>"
    )

    assert mail_message.body == "Test Body Content"


def test_mail_message_display_contents():
    """Test MailMessage.display_contents property with actual email parsing"""
    email_content = """From: sender@example.com
To: recipient@example.com
Subject: Test Subject

Test Body Content"""

    mail_message = MailMessage(
        sha256=b"test",
        content=email_content,
        message_id="<test@example.com>",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        subject="Test Subject",
        size=1024,
        sent_at=datetime(2023, 1, 1, 12, 0, 0),
    )
    mail_message.people = []

    assert mail_message.display_contents == {
        "content": "Test Body Content",
        "date": "2023-01-01T12:00:00",
        "filename": None,
        "mime_type": None,
        "size": 1024,
        "subject": "Test Subject",
        "sender": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "tags": None,
        "folder": None,
        "message_id": "<test@example.com>",
        "people": [],
        "project_id": None,
        "sensitivity": "basic",  # Default value from model
    }


@pytest.mark.parametrize(
    "created_at,expected_date",
    [
        (datetime(2023, 1, 1, 12, 0, 0), "2023-01-01T12:00:00"),
        (None, None),
    ],
)
def test_email_attachment_as_payload(created_at, expected_date):
    """Test EmailAttachment.as_payload method"""
    attachment = EmailAttachment(
        sha256=b"test",
        filename="document.pdf",
        mime_type="application/pdf",
        size=1024,
        mail_message_id=123,
        created_at=created_at,
        tags=["pdf", "document"],
        mail_message=MailMessage(
            sent_at=datetime(2025, 1, 1, 12, 0, 0),
            message_id="123",
            subject="Test",
            sender="john.doe@techcorp.com",
            recipients=["john.doe@techcorp.com"],
            folder="INBOX",
        ),
    )
    # Manually set id for testing
    object.__setattr__(attachment, "id", 456)
    attachment.people = []

    payload = attachment.as_payload()

    expected = {
        "source_id": 456,
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "size": 1024,
        "created_at": expected_date,
        "mail_message_id": 123,
        "tags": ["pdf", "document"],
        "sent_at": "2025-01-01T12:00:00",
        "people": [],
        "project_id": None,
        "sensitivity": "basic",  # Default value from model
    }
    assert payload == expected


@pytest.mark.parametrize(
    "has_filename,content_source,expected_content",
    [
        (True, "file", b"test file content"),
        (False, "content", "attachment content"),
    ],
)
@patch("memory.common.extract.extract_data_chunks")
def test_email_attachment_data_chunks(
    mock_extract, has_filename, content_source, expected_content, tmp_path
):
    """Test EmailAttachment.data_chunks method"""
    from memory.common.extract import DataChunk

    mock_extract.return_value = [
        DataChunk(data=["extracted text"], metadata={"source": content_source})
    ]

    if has_filename:
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test file content")
        attachment = EmailAttachment(
            sha256=b"test",
            filename=str(test_file),
            mime_type="text/plain",
            mail_message_id=123,
        )
    else:
        attachment = EmailAttachment(
            sha256=b"test",
            content="attachment content",
            filename=None,
            mime_type="text/plain",
            mail_message_id=123,
        )

    # Mock _make_chunk to return a simple chunk
    mock_chunk = Mock()
    with patch.object(attachment, "_make_chunk", return_value=mock_chunk) as mock_make:
        result = attachment.data_chunks({"extra": "metadata"})

    # Verify the method calls
    mock_extract.assert_called_once_with("text/plain", expected_content)
    mock_make.assert_called_once_with(
        extract.DataChunk(data=["extracted text"], metadata={"source": content_source}),
        {"extra": "metadata"},
    )
    assert result == [mock_chunk]


def test_email_attachment_cascade_delete(db_session: Session):
    """Test that EmailAttachment is deleted when MailMessage is deleted"""
    mail_message = MailMessage(
        sha256=b"test_email",
        content="test email",
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        folder="INBOX",
    )
    db_session.add(mail_message)
    db_session.commit()

    attachment = EmailAttachment(
        sha256=b"test_attachment",
        content="attachment content",
        mail_message=mail_message,
        filename="test.txt",
        mime_type="text/plain",
        size=100,
        modality="attachment",  # Set modality explicitly
    )
    db_session.add(attachment)
    db_session.commit()

    attachment_id = attachment.id

    # Delete the mail message
    db_session.delete(mail_message)
    db_session.commit()

    # Verify the attachment was also deleted
    deleted_attachment = (
        db_session.query(EmailAttachment).filter_by(id=attachment_id).first()
    )
    assert deleted_attachment is None


@pytest.mark.parametrize(
    "pages,expected_chunks",
    [
        # No pages
        ([], []),
        # Single page
        (["Page 1 content"], [("Page 1 content", {"type": "page"})]),
        # Multiple pages
        (
            ["Page 1", "Page 2", "Page 3"],
            [
                (
                    "Page 1\n\nPage 2\n\nPage 3",
                    {"type": "section", "tags": {"tag1", "tag2"}},
                ),
                ("test summary", {"type": "summary", "tags": {"tag1", "tag2"}}),
            ],
        ),
        # Empty/whitespace pages filtered out
        (["", "  ", "Page 3"], [("Page 3", {"type": "page"})]),
        # All empty - no chunks created
        (["", "  ", "   "], []),
    ],
)
def test_book_section_data_chunks(pages, expected_chunks):
    """Test BookSection.data_chunks with various page combinations"""
    content = "\n\n".join(pages).strip()
    book_section = BookSection(
        sha256=b"test_section",
        content=content,
        modality="book",
        book_id=1,
        start_page=10,
        end_page=10 + len(pages),
        pages=pages,
        book=Book(id=1, title="Test Book", author="Test Author"),
    )

    chunks = book_section.data_chunks()
    expected = [
        (c, extract.merge_metadata(cast(dict[str, Any], book_section.as_payload()), m))
        for c, m in expected_chunks
    ]
    assert [(c.content, c.item_metadata) for c in chunks] == expected
    for c in chunks:
        assert cast(list, c.file_paths) == []


@pytest.mark.parametrize(
    "content,expected",
    [
        ("", []),
        (
            "Short content",
            [
                extract.DataChunk(
                    data=["Short content"], metadata={"tags": ["tag1", "tag2"]}
                )
            ],
        ),
        (
            "This is a very long piece of content that should be chunked into multiple pieces when processed.",
            [
                extract.DataChunk(
                    data=[
                        "This is a very long piece of content that should be chunked into multiple pieces when processed."
                    ],
                    metadata={"tags": ["tag1", "tag2"]},
                ),
                extract.DataChunk(
                    data=["This is a very long piece of content that"],
                    metadata={"tags": ["tag1", "tag2"]},
                ),
                extract.DataChunk(
                    data=["should be chunked into multiple pieces when"],
                    metadata={"tags": ["tag1", "tag2"]},
                ),
                extract.DataChunk(
                    data=["processed."],
                    metadata={"tags": ["tag1", "tag2"]},
                ),
                extract.DataChunk(
                    data=["test summary"],
                    metadata={"tags": ["tag1", "tag2"]},
                ),
            ],
        ),
    ],
)
def test_blog_post_chunk_contents(content, expected, default_chunk_size):
    default_chunk_size(10)
    blog_post = BlogPost(
        sha256=b"test_blog",
        content=content,
        modality="blog",
        url="https://example.com/post",
        images=[],
    )

    with patch.object(chunker, "DEFAULT_CHUNK_TOKENS", 10):
        assert blog_post._chunk_contents() == expected


def test_blog_post_chunk_contents_with_images(tmp_path):
    """Test BlogPost._chunk_contents with images"""
    # Create test image files
    img1_path = tmp_path / "img1.jpg"
    img2_path = tmp_path / "img2.jpg"
    for img_path in [img1_path, img2_path]:
        img = Image.new("RGB", (10, 10), color="red")
        img.save(img_path)

    blog_post = BlogPost(
        sha256=b"test_blog",
        content="Content with images",
        modality="blog",
        url="https://example.com/post",
        images=[str(img1_path), str(img2_path)],
    )

    result = blog_post._chunk_contents()
    result = [
        [i if isinstance(i, str) else getattr(i, "filename") for i in c.data]
        for c in result
    ]
    assert result == [
        ["Content with images", img1_path.as_posix(), img2_path.as_posix()]
    ]


def test_blog_post_chunk_contents_with_image_long_content(tmp_path, default_chunk_size):
    default_chunk_size(10)
    img1_path = tmp_path / "img1.jpg"
    img2_path = tmp_path / "img2.jpg"
    for img_path in [img1_path, img2_path]:
        img = Image.new("RGB", (10, 10), color="red")
        img.save(img_path)

    blog_post = BlogPost(
        sha256=b"test_blog",
        content=f"First picture is here: {img1_path.as_posix()}\nSecond picture is here: {img2_path.as_posix()}",
        modality="blog",
        url="https://example.com/post",
        images=[str(img1_path), str(img2_path)],
    )

    with patch.object(chunker, "DEFAULT_CHUNK_TOKENS", 10):
        result = blog_post._chunk_contents()

    result = [
        [i if isinstance(i, str) else getattr(i, "filename") for i in c.data]
        for c in result
    ]
    # With sentence-boundary chunking and small chunk size, long image paths get
    # split into separate chunks. Each image gets its own chunk with the image
    # appearing twice (once from the text reference, once from the images list).
    assert result == [
        [
            f"First picture is here: {img1_path.as_posix()}\nSecond picture is here: {img2_path.as_posix()}",
            img1_path.as_posix(),
            img2_path.as_posix(),
        ],
        ["First picture is here:"],
        [img1_path.as_posix(), img1_path.as_posix()],
        ["Second picture is here:"],
        [img2_path.as_posix(), img2_path.as_posix()],
        ["test summary"],
    ]


@pytest.mark.parametrize(
    "metadata,expected_semantic_metadata,expected_temporal_metadata,observation_tags",
    [
        (
            {},
            {
                "source_id": None,
                "tags": [],
                "size": None,
                "people": [],
                "project_id": None,
                "sensitivity": "basic",  # Default value from model
                "observation_type": "preference",
                "subject": "programming preferences",
                "confidence": {"observation_accuracy": 0.9},
                "evidence": {
                    "quote": "I really like Python",
                    "context": "discussion about languages",
                },
                "agent_model": "claude-3.5-sonnet",
                "embedding_type": "semantic",
            },
            {
                "source_id": None,
                "tags": [],
                "size": None,
                "people": [],
                "project_id": None,
                "sensitivity": "basic",  # Default value from model
                "observation_type": "preference",
                "subject": "programming preferences",
                "confidence": {"observation_accuracy": 0.9},
                "evidence": {
                    "quote": "I really like Python",
                    "context": "discussion about languages",
                },
                "agent_model": "claude-3.5-sonnet",
                "embedding_type": "temporal",
            },
            [],
        ),
        (
            {"extra_key": "extra_value"},
            {
                "source_id": None,
                "tags": [],
                "size": None,
                "people": [],
                "project_id": None,
                "sensitivity": "basic",  # Default value from model
                "observation_type": "preference",
                "subject": "programming preferences",
                "confidence": {"observation_accuracy": 0.9},
                "evidence": {
                    "quote": "I really like Python",
                    "context": "discussion about languages",
                },
                "agent_model": "claude-3.5-sonnet",
                "extra_key": "extra_value",
                "embedding_type": "semantic",
            },
            {
                "source_id": None,
                "tags": [],
                "size": None,
                "people": [],
                "project_id": None,
                "sensitivity": "basic",  # Default value from model
                "observation_type": "preference",
                "subject": "programming preferences",
                "confidence": {"observation_accuracy": 0.9},
                "evidence": {
                    "quote": "I really like Python",
                    "context": "discussion about languages",
                },
                "agent_model": "claude-3.5-sonnet",
                "extra_key": "extra_value",
                "embedding_type": "temporal",
            },
            [],
        ),
        (
            {"tags": ["existing_tag"], "source": "test"},
            {
                "source_id": None,
                "tags": {"existing_tag"},
                "size": None,
                "people": [],
                "project_id": None,
                "sensitivity": "basic",  # Default value from model
                "observation_type": "preference",
                "subject": "programming preferences",
                "confidence": {"observation_accuracy": 0.9},
                "evidence": {
                    "quote": "I really like Python",
                    "context": "discussion about languages",
                },
                "agent_model": "claude-3.5-sonnet",
                "source": "test",
                "embedding_type": "semantic",
            },
            {
                "source_id": None,
                "tags": {"existing_tag"},
                "size": None,
                "people": [],
                "project_id": None,
                "sensitivity": "basic",  # Default value from model
                "observation_type": "preference",
                "subject": "programming preferences",
                "confidence": {"observation_accuracy": 0.9},
                "evidence": {
                    "quote": "I really like Python",
                    "context": "discussion about languages",
                },
                "agent_model": "claude-3.5-sonnet",
                "source": "test",
                "embedding_type": "temporal",
            },
            [],
        ),
    ],
)
def test_agent_observation_data_chunks(
    metadata, expected_semantic_metadata, expected_temporal_metadata, observation_tags
):
    """Test AgentObservation.data_chunks generates correct chunks with proper metadata"""
    session_id = uuid.uuid4()
    observation = AgentObservation(
        sha256=b"test_obs",
        content="User prefers Python over JavaScript",
        subject="programming preferences",
        observation_type="preference",
        evidence={
            "quote": "I really like Python",
            "context": "discussion about languages",
        },
        agent_model="claude-3.5-sonnet",
        session_id=session_id,
        tags=observation_tags,
    )
    observation.people = []
    observation.update_confidences({"observation_accuracy": 0.9})
    # Set inserted_at using object.__setattr__ to bypass SQLAlchemy restrictions
    object.__setattr__(observation, "inserted_at", datetime(2023, 1, 1, 12, 0, 0))

    result = observation.data_chunks(metadata)

    # Verify chunks
    assert len(result) == 4

    semantic_chunk = result[0]
    expected_semantic_text = "Subject: programming preferences | Type: preference | Observation: User prefers Python over JavaScript | Quote: I really like Python | Context: discussion about languages"
    assert semantic_chunk.data == [expected_semantic_text]

    # Add session_id to expected metadata and remove tags if empty
    expected_semantic_with_session = expected_semantic_metadata.copy()
    expected_semantic_with_session["session_id"] = str(session_id)
    if not expected_semantic_with_session.get("tags"):
        del expected_semantic_with_session["tags"]

    assert semantic_chunk.item_metadata == expected_semantic_with_session
    assert cast(str, semantic_chunk.collection_name) == "semantic"

    temporal_chunk = result[1]
    expected_temporal_text = "Time: 12:00 on Sunday (afternoon) | Subject: programming preferences | Observation: User prefers Python over JavaScript"
    assert temporal_chunk.data == [expected_temporal_text]

    # Add session_id to expected metadata and remove tags if empty
    expected_temporal_with_session = expected_temporal_metadata.copy()
    expected_temporal_with_session["session_id"] = str(session_id)
    if not expected_temporal_with_session.get("tags"):
        del expected_temporal_with_session["tags"]

    assert temporal_chunk.item_metadata == expected_temporal_with_session
    assert cast(str, temporal_chunk.collection_name) == "temporal"


def test_agent_observation_data_chunks_with_none_values():
    """Test AgentObservation.data_chunks handles None values correctly"""
    observation = AgentObservation(
        sha256=b"test_obs",
        content="Content",
        subject="subject",
        observation_type="belief",
        evidence=None,
        agent_model="gpt-4",
        session_id=None,
    )
    observation.update_confidences({"observation_accuracy": 0.7})
    object.__setattr__(observation, "inserted_at", datetime(2023, 2, 15, 9, 30, 0))

    result = observation.data_chunks()

    assert len(result) == 3
    assert cast(str, result[0].collection_name) == "semantic"
    assert cast(str, result[1].collection_name) == "temporal"

    # Verify content with None evidence
    assert [i.data for i in result] == [
        ["Subject: subject | Type: belief | Observation: Content"],
        [
            "Time: 09:30 on Wednesday (morning) | Subject: subject | Observation: Content"
        ],
        ["Content"],
    ]


def test_agent_observation_data_chunks_merge_metadata_behavior():
    """Test that merge_metadata works correctly in data_chunks"""
    observation = AgentObservation(
        sha256=b"test",
        content="test",
        subject="test",
        observation_type="test",
        evidence={},
        agent_model="test",
        tags=["base_tag"],  # Set base tags so they appear in both chunks
    )
    observation.update_confidences({"observation_accuracy": 0.9})
    object.__setattr__(observation, "inserted_at", datetime.now())

    # Test that metadata merging preserves original values and adds new ones
    input_metadata = {"existing": "value", "tags": ["tag1"]}
    result = observation.data_chunks(input_metadata)

    semantic_metadata = result[0].item_metadata
    temporal_metadata = result[1].item_metadata

    # Both should have the existing metadata plus embedding_type
    assert semantic_metadata["existing"] == "value"
    assert semantic_metadata["tags"] == {"tag1", "base_tag"}  # Merged tags
    assert semantic_metadata["embedding_type"] == "semantic"

    assert temporal_metadata["existing"] == "value"
    assert temporal_metadata["tags"] == {"tag1", "base_tag"}  # Merged tags
    assert temporal_metadata["embedding_type"] == "temporal"


@pytest.mark.parametrize(
    "subject, content, expected",
    (
        (None, "bla bla bla", ["bla bla bla"]),
        (None, "    \n\n  bla bla bla  \t\t \n ", ["bla bla bla"]),
        ("my gosh, a subject!", "blee bleee", ["# my gosh, a subject!\n\nblee bleee"]),
    ),
)
def test_note_data_chunks(subject, content, expected):
    note = Note(
        sha256=b"test_obs",
        content=content,
        subject=subject,
        note_type="quicky",
        size=123,
        tags=["bla"],
    )
    note.people = []
    note.update_confidences({"observation_accuracy": 0.9})
    chunks = note.data_chunks()
    assert [chunk.content for chunk in chunks] == expected
    for chunk in chunks:
        assert cast(list, chunk.file_paths) == []
        tags = {"bla"}
        if cast(str, chunk.content) == "test summary":
            tags |= {"tag1", "tag2"}
        assert chunk.item_metadata == {
            "confidence": {"observation_accuracy": 0.9},
            "note_type": "quicky",
            "size": 123,
            "source_id": None,
            "subject": subject,
            "tags": tags,
            "people": [],
            "project_id": None,
            "sensitivity": "basic",  # Default value from model
        }


def test_note_data_chunks_long_content():
    """Test Note.data_chunks() with long content that gets split into multiple chunks."""
    note = Note(
        sha256=b"test_obs_long",
        content=SAMPLE_MARKDOWN,
        subject=None,
        note_type="quicky",
        size=123,
        tags=["bla"],
    )
    note.update_confidences({"observation_accuracy": 0.9})
    chunks = note.data_chunks()

    # Should produce multiple text chunks plus a summary chunk
    assert len(chunks) >= 2, "Long content should produce multiple chunks"

    # The last chunk should be the summary
    assert chunks[-1].content == "test summary"
    assert chunks[-1].item_metadata["tags"] == {"bla", "tag1", "tag2"}

    # All text chunks (except summary) should contain substantial portions of the original
    text_chunks = chunks[:-1]
    all_chunk_text = " ".join(str(chunk.content) for chunk in text_chunks)
    assert "Programming languages" in all_chunk_text
    assert "WebAssembly" in all_chunk_text

    # All chunks should have correct base metadata
    for chunk in chunks:
        assert cast(list, chunk.file_paths) == []
        assert chunk.item_metadata["confidence"] == {"observation_accuracy": 0.9}
        assert chunk.item_metadata["note_type"] == "quicky"
        assert chunk.item_metadata["size"] == 123
        assert chunk.item_metadata["source_id"] is None
        assert chunk.item_metadata["subject"] is None


# --- Meeting Tests ---


def test_meeting_as_payload_with_attendees():
    """Test Meeting.as_payload includes 'people' field when attendees exist."""
    # Create mock people (Person is now a thin identity record)
    person1 = Person(id=1, identifier="person1", display_name="Alice")
    person2 = Person(id=2, identifier="person2", display_name="Bob")

    meeting = Meeting(
        sha256=b"test_meeting",
        content="Meeting transcript",
        title="Team Standup",
    )
    # Set people relationship (data migrated from meeting_attendees to source_item_people)
    meeting.people = [person1, person2]

    payload = meeting.as_payload()

    # Should include 'people' field with attendee IDs
    assert "people" in payload
    assert payload["people"] == [1, 2]


def test_meeting_as_payload_without_attendees():
    """Test Meeting.as_payload with no attendees."""
    meeting = Meeting(
        sha256=b"test_meeting_empty",
        content="Solo meeting notes",
        title="Personal Notes",
    )
    meeting.people = []

    payload = meeting.as_payload()

    # Should have empty people list
    assert payload["people"] == []


def test_meeting_as_payload_single_attendee():
    """Test Meeting.as_payload with single attendee."""
    person = Person(id=42, identifier="single", display_name="Solo")

    meeting = Meeting(
        sha256=b"test_meeting_single",
        content="One on one notes",
        title="1:1 Meeting",
    )
    meeting.people = [person]

    payload = meeting.as_payload()

    assert "people" in payload
    assert payload["people"] == [42]


# ====== truncate_preview tests ======


@pytest.mark.parametrize(
    "text,limit,expected",
    [
        (None, 300, None),
        ("", 300, None),
        ("short text", 300, "short text"),
        ("A" * 300, 300, "A" * 300),
        ("A" * 301, 300, "A" * 300 + "..."),
        ("A" * 500, 300, "A" * 300 + "..."),
        ("hello", 3, "hel..."),
    ],
)
def test_truncate_preview(text, limit, expected):
    assert truncate_preview(text, limit=limit) == expected


# ====== SourceItem.preview_text tests ======


def test_source_item_preview_text_none_content():
    item = SourceItem(sha256=b"test1", modality="test", content=None)
    assert item.preview_text is None


def test_source_item_preview_text_short_content():
    item = SourceItem(sha256=b"test2", modality="test", content="Short content")
    assert item.preview_text == "Short content"


def test_source_item_preview_text_truncates_long_content():
    long = "A" * 500
    item = SourceItem(sha256=b"test3", modality="test", content=long)
    assert item.preview_text == "A" * 300 + "..."


# ====== GitCommit.preview_text tests ======


def test_git_commit_preview_text_uses_diff_summary():
    commit = GitCommit(sha256=b"gc1", modality="git", diff_summary="Added new feature", content="full diff here")
    assert commit.preview_text == "Added new feature"


def test_git_commit_preview_text_falls_back_to_content():
    commit = GitCommit(sha256=b"gc2", modality="git", diff_summary=None, content="commit message")
    assert commit.preview_text == "commit message"


def test_git_commit_preview_text_truncates_long_content_fallback():
    long_content = "D" * 500
    commit = GitCommit(sha256=b"gc3", modality="git", diff_summary=None, content=long_content)
    assert commit.preview_text == "D" * 300 + "..."


def test_git_commit_preview_text_truncates_long_diff_summary():
    long_summary = "S" * 500
    commit = GitCommit(sha256=b"gc4", modality="git", diff_summary=long_summary, content="x")
    assert commit.preview_text == "S" * 300 + "..."


def test_git_commit_preview_text_none_both():
    commit = GitCommit(sha256=b"gc5", modality="git", diff_summary=None, content=None)
    assert commit.preview_text is None


# ====== MailMessage.preview_text tests ======


def test_mail_message_preview_text_falls_back_to_subject_on_exception():
    msg = MailMessage(sha256=b"mm1", content="not valid email", subject="Test Subject")
    # Force an exception from the body property
    with patch.object(MailMessage, "body", new_callable=PropertyMock, side_effect=KeyError("body")):
        assert msg.preview_text == "Test Subject"


def test_mail_message_preview_text_falls_back_to_subject_when_no_body():
    msg = MailMessage(sha256=b"mm2", content="valid", subject="My Subject")
    with patch.object(MailMessage, "body", new_callable=PropertyMock, return_value=""):
        assert msg.preview_text == "My Subject"


def test_mail_message_preview_text_uses_body_when_available():
    msg = MailMessage(sha256=b"mm3", content="valid", subject="My Subject")
    body_text = "This is the parsed email body content."
    with patch.object(MailMessage, "body", new_callable=PropertyMock, return_value=body_text):
        assert msg.preview_text == body_text


def test_mail_message_preview_text_truncates_long_body():
    msg = MailMessage(sha256=b"mm4", content="valid", subject="My Subject")
    long_body = "B" * 500
    with patch.object(MailMessage, "body", new_callable=PropertyMock, return_value=long_body):
        assert msg.preview_text == "B" * 300 + "..."


# ====== Meeting.preview_text tests ======


def test_meeting_preview_text_uses_summary():
    meeting = Meeting(sha256=b"mt1", summary="Meeting summary", notes="Meeting notes", title="Standup")
    assert meeting.preview_text == "Meeting summary"


def test_meeting_preview_text_uses_notes_if_no_summary():
    meeting = Meeting(sha256=b"mt2", summary=None, notes="Notes here", title="Standup")
    assert meeting.preview_text == "Notes here"


def test_meeting_preview_text_falls_back_to_title():
    meeting = Meeting(sha256=b"mt3", summary=None, notes=None, title="My Meeting")
    assert meeting.preview_text == "My Meeting"


def test_meeting_preview_text_truncates_long_summary():
    long_summary = "M" * 500
    meeting = Meeting(sha256=b"mt4", summary=long_summary, title="Meeting")
    assert meeting.preview_text == "M" * 300 + "..."


# ====== EmailAttachment.preview_text tests ======


def test_email_attachment_preview_text_returns_filename():
    attachment = EmailAttachment(sha256=b"ea1", modality="email_attachment", filename="report.pdf")
    assert attachment.preview_text == "report.pdf"


def test_email_attachment_preview_text_none_when_no_filename():
    attachment = EmailAttachment(sha256=b"ea2", modality="email_attachment", filename=None)
    assert attachment.preview_text is None


# ====== DiscordMessage.preview_text tests ======


def test_discord_message_preview_text_returns_title():
    msg = DiscordMessage(sha256=b"dm1", modality="discord", content="Hello world")
    author_mock = Mock()
    author_mock.username = "testuser"
    msg.author = author_mock
    assert msg.preview_text == "testuser: Hello world"


def test_discord_message_preview_text_unknown_author():
    msg = DiscordMessage(sha256=b"dm2", modality="discord", content="Some message")
    msg.author = None
    assert msg.preview_text == "unknown: Some message"


# ====== SlackMessage.preview_text tests ======


def test_slack_message_preview_text_returns_title():
    msg = SlackMessage(sha256=b"sm1", modality="slack", content="Slack message content", author_name="alice")
    assert msg.preview_text == "alice: Slack message content"


def test_slack_message_preview_text_falls_back_to_author_id():
    msg = SlackMessage(sha256=b"sm2", modality="slack", content="hello", author_name=None, author_id="U12345")
    assert msg.preview_text == "U12345: hello"


# ====== Photo.preview_text tests ======


def test_photo_preview_text_with_all_metadata():
    taken = datetime(2024, 6, 15, 10, 30, 0)
    photo = Photo(sha256=b"ph1", modality="photo", filename="sunset.jpg", camera="Canon EOS R5", exif_taken_at=taken)
    assert photo.preview_text == f"sunset.jpg | Canon EOS R5 | {taken}"


def test_photo_preview_text_filename_only():
    photo = Photo(sha256=b"ph2", modality="photo", filename="image.png", camera=None, exif_taken_at=None)
    assert photo.preview_text == "image.png"


def test_photo_preview_text_no_filename_defaults_to_photo():
    photo = Photo(sha256=b"ph3", modality="photo", filename=None, camera=None, exif_taken_at=None)
    assert photo.preview_text == "photo"


def test_photo_preview_text_with_camera_no_date():
    photo = Photo(sha256=b"ph4", modality="photo", filename="pic.jpg", camera="iPhone 15", exif_taken_at=None)
    assert photo.preview_text == "pic.jpg | iPhone 15"


# ====== Comic.preview_text tests ======


def test_comic_preview_text_title_and_author():
    comic = Comic(sha256=b"co1", modality="comic", title="XKCD #42", author="Randall Munroe")
    assert comic.preview_text == "XKCD #42 by Randall Munroe"


def test_comic_preview_text_title_only():
    comic = Comic(sha256=b"co2", modality="comic", title="SMBC #100", author=None)
    assert comic.preview_text == "SMBC #100"


def test_comic_preview_text_no_title_falls_back_to_filename():
    comic = Comic(sha256=b"co3", modality="comic", title=None, author=None, filename="comic_page.png")
    assert comic.preview_text == "comic_page.png"


def test_comic_preview_text_no_title_no_filename():
    comic = Comic(sha256=b"co4", modality="comic", title=None, author=None, filename=None)
    assert comic.preview_text is None
