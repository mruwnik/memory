import pytest

from memory.common import settings
from memory.parsers.google_drive import GoogleCredentials, GoogleDriveClient
from tests.memory.office_files import (
    bomb_docx,
    docx_bytes,
    paragraphs_xml,
    understate_sizes,
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIB = 1024 * 1024


@pytest.fixture
def client() -> GoogleDriveClient:
    credentials = GoogleCredentials(
        access_token="token", refresh_token=None, token_expires_at=None, scopes=[]
    )
    return GoogleDriveClient(credentials, client_id="id", client_secret="secret")


def fetch(client: GoogleDriveClient, monkeypatch, content: bytes) -> str:
    monkeypatch.setattr(client, "export_file", lambda file_id, mime_type: content)
    data = client.fetch_file({"id": "f1", "name": "doc.docx", "mimeType": DOCX_MIME})
    assert data is not None
    return data["content"]


def test_docx_text_is_extracted(client, monkeypatch):
    content = docx_bytes(paragraphs_xml("first", "second"))
    assert fetch(client, monkeypatch, content) == "first\n\nsecond"


@pytest.mark.parametrize(
    "content",
    [bomb_docx(2 * MIB), understate_sizes(bomb_docx(2 * MIB), declared=1000)],
    ids=["bomb", "bomb-declaring-small"],
)
def test_docx_over_the_markup_limit_is_not_parsed(client, monkeypatch, content):
    monkeypatch.setattr(settings, "MAX_OFFICE_MARKUP_BYTES", MIB)
    parsed = []
    monkeypatch.setattr(
        "memory.parsers.google_drive.DefusedElementTree.parse",
        lambda source: parsed.append(source),
    )

    assert fetch(client, monkeypatch, content) == ""
    assert parsed == []
