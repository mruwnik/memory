import hashlib

from memory.common import settings
from memory.common.db.models import MiscDoc


def _write(rel: str, data: bytes) -> str:
    path = settings.MISC_STORAGE_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path.relative_to(settings.FILE_STORAGE_DIR))


def test_misc_doc_chunks_text_file(db_session):
    body = b"hello generic ingestion world\n" * 5
    filename = _write("test_doc.txt", body)
    doc = MiscDoc(
        modality="doc",
        mime_type="text/plain",
        filename=filename,
        sha256=hashlib.sha256(body).digest(),
        size=len(body),
        tags=["t"],
        doc_metadata={"source": "unit-test", "pages": 1},
    )
    db_session.add(doc)
    db_session.flush()

    # JSONB column round-trips through the DB.
    db_session.refresh(doc)
    assert doc.doc_metadata == {"source": "unit-test", "pages": 1}

    chunks = doc._chunk_contents()
    assert chunks  # non-empty

    payload = doc.as_payload()
    assert payload["source"] == "unit-test"
    assert payload["pages"] == 1
    assert payload["content_type"] == "text/plain"


def test_misc_doc_as_payload_metadata_collision(db_session):
    body = b"collision body\n"
    filename = _write("collision_doc.txt", body)
    doc = MiscDoc(
        modality="doc",
        mime_type="text/plain",
        filename=filename,
        sha256=hashlib.sha256(body).digest(),
        size=len(body),
        tags=["t"],
        doc_metadata={
            "source": "x",
            "filename": "SHOULD_NOT_WIN",
            "tags": "SHOULD_NOT_WIN",
        },
    )
    db_session.add(doc)
    db_session.flush()

    # Must not raise despite metadata keys colliding with typed/base fields.
    payload = doc.as_payload()
    assert payload["filename"] == filename
    assert payload["tags"] == ["t"]
    assert payload["source"] == "x"


def test_misc_doc_emits_bibliographic_chunk(db_session):
    body = b"hello generic ingestion world\n" * 5
    filename = _write("anscombe.txt", body)
    doc = MiscDoc(
        modality="doc",
        mime_type="text/plain",
        filename=filename,
        sha256=hashlib.sha256(body).digest(),
        size=len(body),
        doc_metadata={
            "title": "Modern Moral Philosophy",
            "author": "G. E. M. Anscombe",
            "year": 1958,
        },
    )
    db_session.add(doc)
    db_session.flush()

    bib = doc.bibliographic_chunk()
    assert bib is not None
    assert bib.modality == "doc"
    text = bib.data[0]
    assert isinstance(text, str)
    assert "Modern Moral Philosophy" in text
    assert "Anscombe" in text
    assert "1958" in text

    # It rides along with the extracted content chunks.
    assert bib.data[0] in [
        c.data[0] for c in doc._chunk_contents() if isinstance(c.data[0], str)
    ]


def test_misc_doc_bibliographic_chunk_falls_back_to_filename(db_session):
    body = b"plain body with no metadata\n"
    filename = _write("nometa.txt", body)
    doc = MiscDoc(
        modality="doc",
        mime_type="text/plain",
        filename=filename,
        sha256=hashlib.sha256(body).digest(),
        size=len(body),
        doc_metadata={},
    )
    db_session.add(doc)
    db_session.flush()

    # Filename stem is still a usable bibliographic signal without metadata.
    bib = doc.bibliographic_chunk()
    assert bib is not None
    text = bib.data[0]
    assert isinstance(text, str)
    assert "nometa" in text


def test_misc_doc_collections():
    assert set(MiscDoc.get_collections()) >= {"doc", "text"}
