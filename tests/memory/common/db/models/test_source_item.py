from sqlalchemy.orm import Session
from unittest.mock import patch
from typing import cast
import pytest
from PIL import Image
from memory.common import settings, chunker, extract
from memory.common.db.models.source_items import (
    Chunk,
    MailMessage,
)
from memory.common.db.models.source_item import (
    SourceItem,
    image_filenames,
    add_pics,
    clean_filename,
)


@pytest.fixture
def default_chunk_size():
    chunk_length = chunker.DEFAULT_CHUNK_TOKENS
    real_chunker = chunker.chunk_text

    def chunk_text(text: str, max_tokens: int = 0):
        return real_chunker(text, max_tokens=chunk_length)

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
    assert source._chunk_contents() == [
        extract.DataChunk(data=e, modality="text") for e in expected
    ]


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
    assert len(result[0].data) == 1
    assert isinstance(result[0].data[0], Image.Image)


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
    assert result[0].data[0] == "Bla bla"
    assert isinstance(result[1].data[0], Image.Image)


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
        sha256=b"test123",
        content="test",
        modality="text",
        tags=["tag1"],
        size=1024,
    )
    # Create actual image
    image_file = tmp_path / "test.png"
    img = Image.new("RGB", (1, 1), color="red")
    img.save(image_file)
    # Use object.__setattr__ to set filename
    object.__setattr__(img, "filename", str(image_file))

    data = [*texts, img]
    metadata = {"extra": "data"}

    chunk = source._make_chunk(extract.DataChunk(data=data), metadata)

    assert chunk.id is not None
    assert chunk.source == source
    assert cast(str, chunk.content) == expected_content
    assert cast(list[str], chunk.file_paths) == [str(image_file)]
    assert chunk.embedding_model is not None

    # Check that metadata is merged correctly
    expected_payload = {
        "source_id": source.id,
        "tags": {"tag1"},
        "extra": "data",
        "size": 1024,
    }
    assert chunk.item_metadata == expected_payload


def test_source_item_as_payload():
    source = SourceItem(
        id=123,
        sha256=b"test123",
        content="test",
        modality="text",
        tags=["tag1", "tag2"],
        size=1024,
    )

    payload = source.as_payload()
    assert payload == {"source_id": 123, "tags": ["tag1", "tag2"], "size": 1024}


@pytest.mark.parametrize(
    "content,filename",
    [
        ("Test content", None),
        (None, "test.txt"),
        ("Test content", "test.txt"),
        (None, None),
    ],
)
def test_source_item_display_contents(content, filename):
    """Test SourceItem.display_contents property"""
    source = SourceItem(
        sha256=b"test123",
        content=content,
        filename=filename,
        modality="text",
        mime_type="text/plain",
        size=123,
        tags=["bla", "ble"],
    )
    assert source.display_contents == {
        "content": content,
        "filename": filename,
        "mime_type": "text/plain",
        "size": 123,
        "tags": ["bla", "ble"],
    }


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


def test_subclass_deletion_cascades_to_source_item(db_session: Session):
    mail_message = MailMessage(
        sha256=b"test_email_cascade",
        content="test email content",
        message_id="<cascade_test@example.com>",
        subject="Cascade Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        folder="INBOX",
    )
    db_session.add(mail_message)
    db_session.commit()

    source_item_id = mail_message.id
    mail_message_id = mail_message.id

    # Verify both records exist
    assert db_session.query(SourceItem).filter_by(id=source_item_id).first() is not None
    assert (
        db_session.query(MailMessage).filter_by(id=mail_message_id).first() is not None
    )

    # Delete the MailMessage subclass
    db_session.delete(mail_message)
    db_session.commit()

    # Verify both the MailMessage and SourceItem records are deleted
    assert db_session.query(MailMessage).filter_by(id=mail_message_id).first() is None
    assert db_session.query(SourceItem).filter_by(id=source_item_id).first() is None


def test_subclass_deletion_cascades_from_source_item(db_session: Session):
    mail_message = MailMessage(
        sha256=b"test_email_cascade",
        content="test email content",
        message_id="<cascade_test@example.com>",
        subject="Cascade Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        folder="INBOX",
    )
    db_session.add(mail_message)
    db_session.commit()

    source_item_id = mail_message.id
    mail_message_id = mail_message.id

    # Verify both records exist
    source_item = db_session.query(SourceItem).get(source_item_id)
    assert source_item
    assert db_session.query(MailMessage).get(mail_message_id)

    # Delete the MailMessage subclass
    db_session.delete(source_item)
    db_session.commit()

    # Verify both the MailMessage and SourceItem records are deleted
    assert db_session.query(MailMessage).filter_by(id=mail_message_id).first() is None
    assert db_session.query(SourceItem).filter_by(id=source_item_id).first() is None
