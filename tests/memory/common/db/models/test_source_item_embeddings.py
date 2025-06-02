import hashlib
from datetime import datetime
from typing import Sequence, cast
from unittest.mock import ANY, Mock, call

import pymupdf  # PyMuPDF
import pytest

from memory.common import settings
from memory.common.db.models.source_item import Chunk, SourceItem
from memory.common.db.models.source_items import (
    AgentObservation,
    BlogPost,
    BookSection,
    Comic,
    EmailAttachment,
    ForumPost,
    MailMessage,
)
from memory.common.db.models.sources import Book
from memory.common.embedding import embed_source_item
from memory.common.extract import page_to_image
from tests.data.contents import (
    CHUNKS,
    LANG_TIMELINE_HASH,
    SAMPLE_MARKDOWN,
    SAMPLE_TEXT,
    image_hash,
)


def compare_chunks(
    chunks: Sequence[Chunk],
    expected: Sequence[tuple[str | None, list[str], dict]],
):
    data = [
        (c.content, [image_hash(i) for i in c.images], c.item_metadata) for c in chunks
    ]
    assert data == expected


def test_base_source_item_text_embeddings(mock_voyage_client):
    item = SourceItem(
        id=1,
        content=SAMPLE_MARKDOWN,
        mime_type="text/html",
        modality="text",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (CHUNKS[0].strip(), cast(list[str], []), metadata),
        (CHUNKS[1].strip(), cast(list[str], []), metadata),
        ("test summary", [], metadata | {"tags": {"tag1", "tag2", "bla"}}),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert not mock_voyage_client.multimodal_embed.call_count

    assert mock_voyage_client.embed.call_args == call(
        [CHUNKS[0].strip(), CHUNKS[1].strip(), "test summary"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )


def test_base_source_item_mixed_embeddings(mock_voyage_client):
    item = SourceItem(
        id=1,
        content=SAMPLE_MARKDOWN,
        filename=DATA_DIR / "lang_timeline.png",
        mime_type="image/png",
        modality="photo",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (CHUNKS[0].strip(), [], metadata),
        (CHUNKS[1].strip(), [], metadata),
        ("test summary", [], metadata | {"tags": {"tag1", "tag2", "bla"}}),
        (None, [LANG_TIMELINE_HASH], {"size": 3465, "source_id": 1, "tags": {"bla"}}),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert mock_voyage_client.multimodal_embed.call_count == 1

    assert mock_voyage_client.embed.call_args == call(
        [CHUNKS[0].strip(), CHUNKS[1].strip(), "test summary"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )
    assert mock_voyage_client.multimodal_embed.call_args == call(
        [[ANY]],
        model=settings.MIXED_EMBEDDING_MODEL,
        input_type="document",
    )
    assert [
        image_hash(i) for i in mock_voyage_client.multimodal_embed.call_args[0][0][0]
    ] == [LANG_TIMELINE_HASH]


def test_mail_message_embeddings(mock_voyage_client):
    item = MailMessage(
        id=1,
        content=SAMPLE_MARKDOWN,
        mime_type="text/html",
        modality="text",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        message_id="123",
        subject="Test Subject",
        sender="test@example.com",
        recipients=["test@example.com"],
        folder="INBOX",
        sent_at=datetime(2025, 1, 1, 12, 0, 0),
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla", "test@example.com"}
    expected = [
        (CHUNKS[0].strip(), [], metadata),
        (CHUNKS[1].strip(), [], metadata),
        (
            "test summary",
            [],
            metadata | {"tags": {"tag1", "tag2", "bla", "test@example.com"}},
        ),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert not mock_voyage_client.multimodal_embed.call_count

    assert mock_voyage_client.embed.call_args == call(
        [CHUNKS[0].strip(), CHUNKS[1].strip(), "test summary"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )


def test_email_attachment_embeddings_text(mock_voyage_client):
    item = EmailAttachment(
        id=1,
        content=SAMPLE_MARKDOWN,
        mime_type="text/html",
        modality="text",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (CHUNKS[0].strip(), cast(list[str], []), metadata),
        (CHUNKS[1].strip(), cast(list[str], []), metadata),
        (
            "test summary",
            [],
            metadata | {"tags": {"tag1", "tag2", "bla"}},
        ),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert not mock_voyage_client.multimodal_embed.call_count

    assert mock_voyage_client.embed.call_args == call(
        [CHUNKS[0].strip(), CHUNKS[1].strip(), "test summary"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )


def test_email_attachment_embeddings_photo(mock_voyage_client):
    item = EmailAttachment(
        id=1,
        content=SAMPLE_MARKDOWN,
        filename=DATA_DIR / "lang_timeline.png",
        mime_type="image/png",
        modality="photo",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (None, [LANG_TIMELINE_HASH], metadata),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 0
    assert mock_voyage_client.multimodal_embed.call_count == 1

    assert mock_voyage_client.multimodal_embed.call_args == call(
        [[ANY]],
        model=settings.MIXED_EMBEDDING_MODEL,
        input_type="document",
    )
    assert [
        image_hash(i) for i in mock_voyage_client.multimodal_embed.call_args[0][0][0]
    ] == [LANG_TIMELINE_HASH]


def test_email_attachment_embeddings_pdf(mock_voyage_client):
    item = EmailAttachment(
        id=1,
        content=SAMPLE_MARKDOWN,
        filename=DATA_DIR / "regulamin.pdf",
        mime_type="application/pdf",
        modality="doc",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    with pymupdf.open(item.filename) as pdf:
        expected = [
            (
                None,
                [image_hash(page_to_image(page))],
                metadata
                | {
                    "page": page.number,
                    "width": page.rect.width,
                    "height": page.rect.height,
                },
            )
            for page in pdf.pages()
        ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 0
    assert mock_voyage_client.multimodal_embed.call_count == 1

    assert mock_voyage_client.multimodal_embed.call_args == call(
        [[ANY], [ANY]],
        model=settings.MIXED_EMBEDDING_MODEL,
        input_type="document",
    )
    assert [
        [image_hash(a) for a in i]
        for i in mock_voyage_client.multimodal_embed.call_args[0][0]
    ] == [page for _, page, _ in expected]


def test_email_attachment_embeddings_comic(mock_voyage_client):
    item = Comic(
        id=1,
        content=SAMPLE_MARKDOWN,
        filename=DATA_DIR / "lang_timeline.png",
        mime_type="image/png",
        modality="comic",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        title="The Evolution of Programming Languages",
        author="John Doe",
        published=datetime(2025, 1, 1, 12, 0, 0),
        volume="1",
        issue="1",
        page=1,
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (
            "The Evolution of Programming Languages by John Doe",
            [LANG_TIMELINE_HASH],
            metadata,
        ),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 0
    assert mock_voyage_client.multimodal_embed.call_count == 1

    assert mock_voyage_client.multimodal_embed.call_args == call(
        [["The Evolution of Programming Languages by John Doe", ANY]],
        model=settings.MIXED_EMBEDDING_MODEL,
        input_type="document",
    )
    assert (
        image_hash(mock_voyage_client.multimodal_embed.call_args[0][0][0][1])
        == LANG_TIMELINE_HASH
    )


def test_book_section_embeddings_single_page(mock_voyage_client):
    item = BookSection(
        id=1,
        content=SAMPLE_MARKDOWN,
        mime_type="text/html",
        modality="text",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        book_id=1,
        section_title="The Evolution of Programming Languages",
        section_number=1,
        section_level=1,
        start_page=1,
        end_page=1,
        pages=[SAMPLE_TEXT],
        book=Book(
            id=1,
            title="Programming Languages",
            author="John Doe",
            published=datetime(2025, 1, 1, 12, 0, 0),
        ),
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (CHUNKS[0].strip(), cast(list[str], []), metadata | {"type": "page"}),
        (CHUNKS[1].strip(), cast(list[str], []), metadata | {"type": "page"}),
        (
            "test summary",
            [],
            metadata | {"tags": {"tag1", "tag2", "bla"}, "type": "summary"},
        ),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert not mock_voyage_client.multimodal_embed.call_count

    assert mock_voyage_client.embed.call_args == call(
        [CHUNKS[0].strip(), CHUNKS[1].strip(), "test summary"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )


def test_book_section_embeddings_multiple_pages(mock_voyage_client):
    item = BookSection(
        id=1,
        content=SAMPLE_MARKDOWN + "\n\n" + SECOND_PAGE,
        mime_type="text/html",
        modality="text",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        book_id=1,
        section_title="The Evolution of Programming Languages",
        section_number=1,
        section_level=1,
        start_page=1,
        end_page=2,
        pages=[SAMPLE_TEXT, SECOND_PAGE_TEXT],
        book=Book(
            id=1,
            title="Programming Languages",
            author="John Doe",
            published=datetime(2025, 1, 1, 12, 0, 0),
        ),
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla", "tag1", "tag2"}
    expected = [
        (item.content.strip(), cast(list[str], []), metadata | {"type": "section"}),
        ("test summary", [], metadata | {"type": "summary"}),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert not mock_voyage_client.multimodal_embed.call_count

    assert mock_voyage_client.embed.call_args == call(
        [item.content.strip(), "test summary"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )


@pytest.mark.parametrize(
    "class_, modality",
    (
        (BlogPost, "blog"),
        (ForumPost, "forum"),
    ),
)
def test_post_embeddings_single_page(mock_voyage_client, class_, modality):
    item = class_(
        id=1,
        content=SAMPLE_MARKDOWN,
        mime_type="text/html",
        modality=modality,
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        images=[LANG_TIMELINE.filename, CODE_COMPLEXITY.filename],  # type: ignore
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla", "tag1", "tag2"}
    expected = [
        (item.content.strip(), [LANG_TIMELINE_HASH, CODE_COMPLEXITY_HASH], metadata),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert not mock_voyage_client.embed.call_count
    assert mock_voyage_client.multimodal_embed.call_count == 1

    assert mock_voyage_client.multimodal_embed.call_args == call(
        [[item.content.strip(), ANY, ANY]],
        model=settings.MIXED_EMBEDDING_MODEL,
        input_type="document",
    )
    assert [
        image_hash(i)
        for i in mock_voyage_client.multimodal_embed.call_args[0][0][0][1:]
    ] == [LANG_TIMELINE_HASH, CODE_COMPLEXITY_HASH]


@pytest.mark.parametrize(
    "class_, modality",
    (
        (BlogPost, "blog"),
        (ForumPost, "forum"),
    ),
)
def test_post_embeddings_multi_page(mock_voyage_client, class_, modality):
    item = class_(
        id=1,
        content=SAMPLE_MARKDOWN + "\n\n" + SECOND_PAGE_MARKDOWN,
        mime_type="text/html",
        modality=modality,
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN + SECOND_PAGE_MARKDOWN),
        tags=["bla"],
        images=[LANG_TIMELINE.filename, CODE_COMPLEXITY.filename],  # type: ignore
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla", "tag1", "tag2"}

    all_contents = (
        item.content.strip(),
        [LANG_TIMELINE_HASH, CODE_COMPLEXITY_HASH],
        metadata,
    )
    first_chunk = (
        TWO_PAGE_CHUNKS[0].strip(),
        [LANG_TIMELINE_HASH, CODE_COMPLEXITY_HASH],
        metadata,
    )
    second_chunk = (TWO_PAGE_CHUNKS[1].strip(), [], metadata)
    third_chunk = (TWO_PAGE_CHUNKS[2].strip(), [], metadata)
    summary = ("test summary", [], metadata)

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(
        item.data_chunks(),
        [all_contents, first_chunk, second_chunk, third_chunk, summary],
    )
    # embed_source_item first does text embedding, then mixed embedding
    # so the order of chunks is different than in data_chunks()
    compare_chunks(
        embed_source_item(item),
        [
            second_chunk,
            third_chunk,
            summary,
            all_contents,
            first_chunk,
        ],
    )

    assert mock_voyage_client.embed.call_count == 1
    assert mock_voyage_client.multimodal_embed.call_count == 1

    assert mock_voyage_client.embed.call_args == call(
        [
            TWO_PAGE_CHUNKS[1].strip(),
            TWO_PAGE_CHUNKS[2].strip(),
            "test summary",
        ],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )
    assert mock_voyage_client.multimodal_embed.call_args == call(
        [[item.content.strip(), ANY, ANY], [TWO_PAGE_CHUNKS[0].strip(), ANY, ANY]],
        model=settings.MIXED_EMBEDDING_MODEL,
        input_type="document",
    )
    assert [
        image_hash(i)
        for i in mock_voyage_client.multimodal_embed.call_args[0][0][0][1:]
    ] == [LANG_TIMELINE_HASH, CODE_COMPLEXITY_HASH]
    assert [
        image_hash(i)
        for i in mock_voyage_client.multimodal_embed.call_args[0][0][1][1:]
    ] == [LANG_TIMELINE_HASH, CODE_COMPLEXITY_HASH]


def test_agent_observation_embeddings(mock_voyage_client):
    item = AgentObservation(
        id=1,
        content="The user thinks that all men must die.",
        mime_type="text/html",
        modality="observation",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        observation_type="belief",
        subject="humans",
        confidence=0.8,
        evidence={
            "quote": "All humans are mortal.",
            "source": "https://en.wikipedia.org/wiki/Human",
        },
        agent_model="gpt-4o",
        inserted_at=datetime(2025, 1, 1, 12, 0, 0),
    )
    metadata = item.as_payload()
    metadata["tags"] = {"bla"}
    expected = [
        (
            "Subject: humans | Type: belief | Observation: The user thinks that all men must die. | Quote: All humans are mortal.",
            [],
            metadata | {"embedding_type": "semantic"},
        ),
        (
            "Time: 12:00 on Wednesday (afternoon) | Subject: humans | Observation: The user thinks that all men must die. | Confidence: 0.8",
            [],
            metadata | {"embedding_type": "temporal"},
        ),
        (
            "The user thinks that all men must die.",
            [],
            metadata | {"embedding_type": "semantic"},
        ),
        ("All humans are mortal.", [], metadata | {"embedding_type": "semantic"}),
    ]

    mock_voyage_client.embed = Mock(return_value=Mock(embeddings=[[0.1] * 1024] * 3))
    mock_voyage_client.multimodal_embed = Mock(
        return_value=Mock(embeddings=[[0.1] * 1024] * 3)
    )
    compare_chunks(item.data_chunks(), expected)
    compare_chunks(embed_source_item(item), expected)

    assert mock_voyage_client.embed.call_count == 1
    assert not mock_voyage_client.multimodal_embed.call_count

    assert mock_voyage_client.embed.call_args == call(
        [
            "Subject: humans | Type: belief | Observation: The user thinks that all men must die. | Quote: All humans are mortal.",
            "Time: 12:00 on Wednesday (afternoon) | Subject: humans | Observation: The user thinks that all men must die. | Confidence: 0.8",
            "The user thinks that all men must die.",
            "All humans are mortal.",
        ],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )
