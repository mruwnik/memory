"""Small DOCX files built in memory for parser tests."""

import io
import struct
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx_bytes(document_xml: bytes, **entries: bytes) -> bytes:
    """A deflated zip holding ``word/document.xml`` and any other ``entries``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def paragraphs_xml(*texts: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in texts)
    return f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'.encode()


def bomb_docx(markup_bytes: int) -> bytes:
    """A DOCX whose document.xml inflates to about ``markup_bytes``."""
    padding = b"<w:p/>" * (markup_bytes // 6)
    return docx_bytes(
        f'<w:document xmlns:w="{W_NS}"><w:body>'.encode() + padding + b"</w:body></w:document>"
    )


def understate_sizes(data: bytes, declared: int) -> bytes:
    """``data`` (a one-entry zip) with the entry's uncompressed size, in both
    its local header and its central directory record, set to ``declared``."""
    out = bytearray(data)
    struct.pack_into("<L", out, 22, declared)
    central = out.rindex(b"PK\x01\x02")
    struct.pack_into("<L", out, central + 24, declared)
    return bytes(out)
