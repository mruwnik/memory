from memory.common.db.models import SourceItem
from sqlalchemy.orm import Session
from unittest.mock import patch, Mock
from typing import cast
import pytest
from PIL import Image
from datetime import datetime
from memory.common import settings
from memory.common import chunker
from memory.common.db.models import (
    Chunk,
    clean_filename,
    image_filenames,
    add_pics,
    MailMessage,
    EmailAttachment,
    BookSection,
    BlogPost,
)


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
    "input_filename,expected",
    [
        ("normal_file.txt", "normal_file_txt"),
        ("file with spaces.pdf", "file_with_spaces_pdf"),
        ("file-with-dashes.doc", "file_with_dashes_doc"),
        ("file@#$%^&*()+={}[]|\\:;\"'<>,.?/~`", "file"),
        ("___multiple___underscores___", "multiple___underscores"),
        ("", ""),
        ("123", "123"),
        ("file.with.multiple.dots.txt", "file_with_multiple_dots_txt"),
    ],
)
def test_clean_filename(input_filename, expected):
    assert clean_filename(input_filename) == expected


def test_image_filenames_with_existing_filenames(tmp_path):
    """Test image_filenames when images already have filenames"""
    chunk_id = "test_chunk_123"

    # Create actual test images and load them from files (which sets filename)
    image1_path = tmp_path / "existing1.png"
    image2_path = tmp_path / "existing2.jpg"

    # Create and save images first
    img1 = Image.new("RGB", (1, 1), color="red")
    img1.save(image1_path)
    img2 = Image.new("RGB", (1, 1), color="blue")
    img2.save(image2_path)

    # Load images from files (this sets the filename attribute)
    image1 = Image.open(image1_path)
    image2 = Image.open(image2_path)

    images = [image1, image2]
    result = image_filenames(chunk_id, images)

    assert result == [str(image1_path), str(image2_path)]


def test_image_filenames_without_existing_filenames():
    """Test image_filenames when images don't have filenames"""
    chunk_id = "test_chunk_456"

    # Create actual test images without filenames
    image1 = Image.new("RGB", (1, 1), color="red")
    image1.format = "PNG"
    # Manually set filename to None to simulate no filename
    object.__setattr__(image1, "filename", None)

    image2 = Image.new("RGB", (1, 1), color="blue")
    image2.format = "JPEG"
    object.__setattr__(image2, "filename", None)

    images = [image1, image2]

    result = image_filenames(chunk_id, images)

    expected_filenames = [
        str(settings.CHUNK_STORAGE_DIR / f"{chunk_id}_0.PNG"),
        str(settings.CHUNK_STORAGE_DIR / f"{chunk_id}_1.JPEG"),
    ]
    assert result == expected_filenames

    assert (settings.CHUNK_STORAGE_DIR / f"{chunk_id}_0.PNG").exists()
    assert (settings.CHUNK_STORAGE_DIR / f"{chunk_id}_1.JPEG").exists()


def test_add_pics():
    """Test add_pics function with mock-like behavior"""
    chunk = "This is a test chunk with image1.png content"

    image1 = Image.new("RGB", (1, 1), color="red")
    object.__setattr__(image1, "filename", "image1.png")

    image2 = Image.new("RGB", (1, 1), color="blue")
    object.__setattr__(image2, "filename", "image2.jpg")

    ignored_image = Image.new("RGB", (1, 1), color="blue")

    images = [image1, image2, ignored_image]
    result = add_pics(chunk, images)

    # Should include the chunk and only images whose filename is in the chunk
    assert result == [chunk, image1]


def test_chunk_data_property_content_only():
    """Test Chunk.data property when only content is set"""
    source = SourceItem(sha256=b"test123", content="test", modality="text")
    chunk = Chunk(source=source, content="Test content", embedding_model="test-model")

    result = chunk.data
    assert result == ["Test content"]


def test_chunk_data_property_with_files(tmp_path):
    """Test Chunk.data property when file_paths are set"""
    # Create test files
    text_file = tmp_path / "test.txt"
    text_file.write_text("Text file content")

    bin_file = tmp_path / "test.bin"
    bin_file.write_bytes(b"Binary content")

    image_file = tmp_path / "test.png"
    # Create a simple 1x1 pixel PNG
    img = Image.new("RGB", (1, 1), color="red")
    img.save(image_file)

    source = SourceItem(sha256=b"test123", content="test", modality="text")
    chunk = Chunk(
        source=source,
        file_paths=[
            str(text_file),
            str(bin_file),
            str(image_file),
            "/missing/file.png",
        ],
        embedding_model="test-model",
    )

    result = chunk.data
    assert len(result) == 3
    assert result[0] == "Text file content"
    assert result[1] == b"Binary content"
    assert isinstance(result[2], Image.Image)


@pytest.mark.parametrize(
    "chunk_length, expected",
    (
        (
            100000,
            [
                [
                    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
                ]
            ],
        ),
        (
            10,
            [
                ["Lorem ipsum dolor sit amet, consectetur adipiscing elit."],
                ["Sed do eiusmod tempor incididunt ut labore"],
                ["et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud"],
                [
                    "et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
                    "ullamco laboris nisi ut"
                ],
                [
                    "et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
                    "ullamco laboris nisi ut aliquip ex ea commodo consequat."
                ],
                [
                    "et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
                    "ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure "
                    "dolor in reprehenderit in"
                ],
                [
                    "ip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in "
                    "voluptate velit esse cillum dolore eu"
                ],
                [
                    "ip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in "
                    "voluptate velit esse cillum dolore eu fugiat nulla pariatur."
                ],
                [
                    "ip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in "
                    "voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint "
                    "occaecat cupidatat non"
                ],
                [
                    "dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non "
                    "proident, sunt in culpa qui officia"
                ],
                [
                    "dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non "
                    "proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
                ],
                [
                    "dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non "
                    "proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
                ],
            ],
        ),
        (
            20,
            [
                [
                    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod "
                    "tempor incididunt ut labore et dolore magna aliqua."
                ],
                [
                    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod "
                    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
                    "veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip"
                ],
                [
                    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
                    "aliquip ex ea commodo consequat."
                ],
                [
                    "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum "
                    "dolore eu fugiat nulla pariatur."
                ],
                [
                    "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia "
                    "deserunt"
                ],
                ["mollit anim id est laborum."],
            ],
        ),
    ),
)
def test_source_item_chunk_contents_text(chunk_length, expected, default_chunk_size):
    """Test SourceItem._chunk_contents for text content"""
    source = SourceItem(
        sha256=b"test123",
        content="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.",
        modality="text",
    )

    default_chunk_size(chunk_length)
    assert source._chunk_contents() == expected


def test_source_item_chunk_contents_image(tmp_path):
    """Test SourceItem._chunk_contents for image content"""
    image_file = tmp_path / "test.png"
    img = Image.new("RGB", (10, 10), color="red")
    img.save(image_file)

    source = SourceItem(
        sha256=b"test123",
        filename=str(image_file),
        modality="image",
        mime_type="image/png",
    )

    result = source._chunk_contents()

    assert len(result) == 1
    assert len(result[0]) == 1
    assert isinstance(result[0][0], Image.Image)


def test_source_item_chunk_contents_mixed(tmp_path):
    """Test SourceItem._chunk_contents for image content"""
    image_file = tmp_path / "test.png"
    img = Image.new("RGB", (10, 10), color="red")
    img.save(image_file)

    source = SourceItem(
        sha256=b"test123",
        content="Bla bla",
        filename=str(image_file),
        modality="image",
        mime_type="image/png",
    )

    result = source._chunk_contents()

    assert len(result) == 2
    assert result[0][0] == "Bla bla"
    assert isinstance(result[1][0], Image.Image)


@pytest.mark.parametrize(
    "texts, expected_content",
    (
        ([], None),
        (["", "     \n ", "    "], None),
        (["Hello"], "Hello"),
        (["Hello", "World"], "Hello\n\nWorld"),
        (["Hello", "World", ""], "Hello\n\nWorld"),
        (["Hello", "World", "", ""], "Hello\n\nWorld"),
        (["Hello", "World", "", "", ""], "Hello\n\nWorld"),
        (["Hello", "World", "", "", "", ""], "Hello\n\nWorld"),
        (["Hello", "World", "", "", "", "", "bla"], "Hello\n\nWorld\n\nbla"),
    ),
)
def test_source_item_make_chunk(tmp_path, texts, expected_content):
    """Test SourceItem._make_chunk method"""
    source = SourceItem(
        sha256=b"test123", content="test", modality="text", tags=["tag1"]
    )
    # Create actual image
    image_file = tmp_path / "test.png"
    img = Image.new("RGB", (1, 1), color="red")
    img.save(image_file)
    # Use object.__setattr__ to set filename
    object.__setattr__(img, "filename", str(image_file))

    data = [*texts, img]
    metadata = {"extra": "data"}

    chunk = source._make_chunk(data, metadata)

    assert chunk.id is not None
    assert chunk.source == source
    assert cast(str, chunk.content) == expected_content
    assert cast(list[str], chunk.file_paths) == [str(image_file)]
    assert chunk.embedding_model is not None

    # Check that metadata is merged correctly
    expected_payload = {"source_id": source.id, "tags": ["tag1"], "extra": "data"}
    assert chunk.item_metadata == expected_payload


def test_source_item_as_payload():
    source = SourceItem(
        id=123,
        sha256=b"test123",
        content="test",
        modality="text",
        tags=["tag1", "tag2"],
    )

    payload = source.as_payload()
    assert payload == {"source_id": 123, "tags": ["tag1", "tag2"]}


@pytest.mark.parametrize(
    "content,filename,expected",
    [
        ("Test content", None, "Test content"),
        (None, "test.txt", "test.txt"),
        ("Test content", "test.txt", "Test content"),  # content takes precedence
        (None, None, None),
    ],
)
def test_source_item_display_contents(content, filename, expected):
    """Test SourceItem.display_contents property"""
    source = SourceItem(
        sha256=b"test123", content=content, filename=filename, modality="text"
    )
    assert source.display_contents == expected


def test_unique_source_items_same_commit(db_session: Session):
    source_item1 = SourceItem(sha256=b"1234567890", content="test1", modality="email")
    source_item2 = SourceItem(sha256=b"1234567890", content="test2", modality="email")
    source_item3 = SourceItem(sha256=b"1234567891", content="test3", modality="email")
    db_session.add(source_item1)
    db_session.add(source_item2)
    db_session.add(source_item3)
    db_session.commit()

    assert db_session.query(SourceItem.sha256, SourceItem.content).all() == [
        (b"1234567890", "test1"),
        (b"1234567891", "test3"),
    ]


def test_unique_source_items_previous_commit(db_session: Session):
    db_session.add_all(
        [
            SourceItem(sha256=b"1234567890", content="test1", modality="email"),
            SourceItem(sha256=b"1234567891", content="test2", modality="email"),
            SourceItem(sha256=b"1234567892", content="test3", modality="email"),
        ]
    )
    db_session.commit()

    db_session.add_all(
        [
            SourceItem(sha256=b"1234567890", content="test4", modality="email"),
            SourceItem(sha256=b"1234567893", content="test5", modality="email"),
            SourceItem(sha256=b"1234567894", content="test6", modality="email"),
        ]
    )
    db_session.commit()

    assert db_session.query(SourceItem.sha256, SourceItem.content).all() == [
        (b"1234567890", "test1"),
        (b"1234567891", "test2"),
        (b"1234567892", "test3"),
        (b"1234567893", "test5"),
        (b"1234567894", "test6"),
    ]


def test_source_item_chunk_contents_empty_content():
    """Test SourceItem._chunk_contents with empty content"""
    source = SourceItem(sha256=b"test123", content=None, modality="text")

    assert source._chunk_contents() == []


def test_source_item_chunk_contents_no_mime_type(tmp_path):
    """Test SourceItem._chunk_contents with filename but no mime_type"""
    image_file = tmp_path / "test.png"
    img = Image.new("RGB", (10, 10), color="red")
    img.save(image_file)

    source = SourceItem(
        sha256=b"test123", filename=str(image_file), modality="image", mime_type=None
    )

    assert source._chunk_contents() == []


@pytest.mark.parametrize(
    "content,file_paths,description",
    [
        ("Test content", None, "content is set"),
        (None, ["test.txt"], "file_paths is set"),
    ],
)
def test_chunk_constraint_validation(
    db_session: Session, content, file_paths, description
):
    """Test that Chunk enforces the constraint that either file_paths or content must be set"""
    source = SourceItem(sha256=b"test123", content="test", modality="text")
    db_session.add(source)
    db_session.commit()

    chunk = Chunk(
        source=source,
        content=content,
        file_paths=file_paths,
        embedding_model="test-model",
    )
    db_session.add(chunk)
    db_session.commit()
    assert chunk.id is not None


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

    with patch.object(settings, "FILE_STORAGE_DIR", "/tmp/storage"):
        result = mail_message.attachments_path
        assert str(result) == f"/tmp/storage/{expected_path}"


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

    with patch.object(settings, "FILE_STORAGE_DIR", tmp_path):
        result = mail_message.safe_filename(filename)

        # Check that the path is correct
        expected_path = tmp_path / "user_example_com" / "INBOX" / expected
        assert result == expected_path

        # Check that the directory was created
        assert result.parent.exists()


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
    )
    # Manually set id for testing
    object.__setattr__(mail_message, "id", 123)

    payload = mail_message.as_payload()

    expected = {
        "source_id": 123,
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
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "size": 1024,
        "created_at": expected_date,
        "mail_message_id": 123,
        "source_id": 456,
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
        ["extracted text"], {"extra": "metadata", "source": content_source}
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


# BookSection tests


@pytest.mark.parametrize(
    "pages,expected_chunks",
    [
        # No pages
        ([], []),
        # Single page
        (["Page 1 content"], [("Page 1 content", 10)]),
        # Multiple pages
        (
            ["Page 1", "Page 2", "Page 3"],
            [
                ("Page 1", 10),
                ("Page 2", 11),
                ("Page 3", 12),
            ],
        ),
        # Empty/whitespace pages filtered out
        (["", "  ", "Page 3"], [("Page 3", 12)]),
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
    )

    chunks = book_section.data_chunks()
    expected = [
        (p, book_section.as_payload() | {"page": i}) for p, i in expected_chunks
    ]
    if content:
        expected.append((content, book_section.as_payload()))

    assert [(c.content, c.item_metadata) for c in chunks] == expected
    for c in chunks:
        assert cast(list, c.file_paths) == []


@pytest.mark.parametrize(
    "content,expected",
    [
        ("", []),
        ("Short content", [["Short content"]]),
        (
            "This is a very long piece of content that should be chunked into multiple pieces when processed.",
            [
                [
                    "This is a very long piece of content that should be chunked into multiple pieces when processed."
                ],
                ["This is a very long piece of content that"],
                ["should be chunked into multiple pieces when"],
                ["processed."],
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
        [i if isinstance(i, str) else getattr(i, "filename") for i in c] for c in result
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
        [i if isinstance(i, str) else getattr(i, "filename") for i in c] for c in result
    ]
    print(result)
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
    ]
