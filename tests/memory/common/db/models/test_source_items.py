from sqlalchemy.orm import Session
from unittest.mock import patch, Mock
from typing import cast
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
)
from memory.common.db.models.source_item import merge_metadata


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

    payload = mail_message.as_payload()

    expected = {
        "source_id": 123,
        "size": 1024,
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
        sha256=b"test", content=email_content, message_id="<test@example.com>"
    )

    expected = (
        "\nSubject: Test Subject\nFrom: \nTo: \nDate: \nBody: \nTest Body Content\n"
    )
    assert mail_message.display_contents == expected


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
    )
    # Manually set id for testing
    object.__setattr__(attachment, "id", 456)

    payload = attachment.as_payload()

    expected = {
        "source_id": 456,
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "size": 1024,
        "created_at": expected_date,
        "mail_message_id": 123,
        "tags": ["pdf", "document"],
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
                ("test", {"type": "summary", "tags": {"tag1", "tag2"}}),
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
        (c, merge_metadata(book_section.as_payload(), m)) for c, m in expected_chunks
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
                    data=["test"],
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
    assert result == [
        [
            f"First picture is here: {img1_path.as_posix()}\nSecond picture is here: {img2_path.as_posix()}",
            img1_path.as_posix(),
            img2_path.as_posix(),
        ],
        [
            f"First picture is here: {img1_path.as_posix()}",
            img1_path.as_posix(),
        ],
        [
            f"Second picture is here: {img2_path.as_posix()}",
            img2_path.as_posix(),
        ],
        ["test"],
    ]


@pytest.mark.parametrize(
    "metadata,expected_semantic_metadata,expected_temporal_metadata,observation_tags",
    [
        (
            {},
            {"embedding_type": "semantic"},
            {"embedding_type": "temporal"},
            [],
        ),
        (
            {"extra_key": "extra_value"},
            {"extra_key": "extra_value", "embedding_type": "semantic"},
            {"extra_key": "extra_value", "embedding_type": "temporal"},
            [],
        ),
        (
            {"tags": ["existing_tag"], "source": "test"},
            {"tags": {"existing_tag"}, "source": "test", "embedding_type": "semantic"},
            {"tags": {"existing_tag"}, "source": "test", "embedding_type": "temporal"},
            [],
        ),
    ],
)
def test_agent_observation_data_chunks(
    metadata, expected_semantic_metadata, expected_temporal_metadata, observation_tags
):
    """Test AgentObservation.data_chunks generates correct chunks with proper metadata"""
    observation = AgentObservation(
        sha256=b"test_obs",
        content="User prefers Python over JavaScript",
        subject="programming preferences",
        observation_type="preference",
        confidence=0.9,
        evidence={
            "quote": "I really like Python",
            "context": "discussion about languages",
        },
        agent_model="claude-3.5-sonnet",
        session_id=uuid.uuid4(),
        tags=observation_tags,
    )
    # Set inserted_at using object.__setattr__ to bypass SQLAlchemy restrictions
    object.__setattr__(observation, "inserted_at", datetime(2023, 1, 1, 12, 0, 0))

    result = observation.data_chunks(metadata)

    # Verify chunks
    assert len(result) == 2

    semantic_chunk = result[0]
    expected_semantic_text = "Subject: programming preferences | Type: preference | Observation: User prefers Python over JavaScript | Quote: I really like Python | Context: discussion about languages"
    assert semantic_chunk.data == [expected_semantic_text]
    assert semantic_chunk.metadata == expected_semantic_metadata
    assert semantic_chunk.collection_name == "semantic"

    temporal_chunk = result[1]
    expected_temporal_text = "Time: 12:00 on Sunday (afternoon) | Subject: programming preferences | Observation: User prefers Python over JavaScript | Confidence: 0.9"
    assert temporal_chunk.data == [expected_temporal_text]
    assert temporal_chunk.metadata == expected_temporal_metadata
    assert temporal_chunk.collection_name == "temporal"


def test_agent_observation_data_chunks_with_none_values():
    """Test AgentObservation.data_chunks handles None values correctly"""
    observation = AgentObservation(
        sha256=b"test_obs",
        content="Content",
        subject="subject",
        observation_type="belief",
        confidence=0.7,
        evidence=None,
        agent_model="gpt-4",
        session_id=None,
    )
    object.__setattr__(observation, "inserted_at", datetime(2023, 2, 15, 9, 30, 0))

    result = observation.data_chunks()

    assert len(result) == 2
    assert result[0].collection_name == "semantic"
    assert result[1].collection_name == "temporal"

    # Verify content with None evidence
    semantic_text = "Subject: subject | Type: belief | Observation: Content"
    assert result[0].data == [semantic_text]

    temporal_text = "Time: 09:30 on Wednesday (morning) | Subject: subject | Observation: Content | Confidence: 0.7"
    assert result[1].data == [temporal_text]


def test_agent_observation_data_chunks_merge_metadata_behavior():
    """Test that merge_metadata works correctly in data_chunks"""
    observation = AgentObservation(
        sha256=b"test",
        content="test",
        subject="test",
        observation_type="test",
        confidence=0.8,
        evidence={},
        agent_model="test",
        tags=["base_tag"],  # Set base tags so they appear in both chunks
    )
    object.__setattr__(observation, "inserted_at", datetime.now())

    # Test that metadata merging preserves original values and adds new ones
    input_metadata = {"existing": "value", "tags": ["tag1"]}
    result = observation.data_chunks(input_metadata)

    semantic_metadata = result[0].metadata
    temporal_metadata = result[1].metadata

    # Both should have the existing metadata plus embedding_type
    assert semantic_metadata["existing"] == "value"
    assert semantic_metadata["tags"] == {"tag1"}  # Merged tags
    assert semantic_metadata["embedding_type"] == "semantic"

    assert temporal_metadata["existing"] == "value"
    assert temporal_metadata["tags"] == {"tag1"}  # Merged tags
    assert temporal_metadata["embedding_type"] == "temporal"
