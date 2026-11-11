"""Small DOCX files built in memory for parser tests."""

import io
import struct
import zipfile
import zlib

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


def set_local_header_field(data: bytes, offset: int, fmt: str, value: int) -> bytes:
    """``data`` (a zip whose first entry starts at 0) with one field of that
    entry's local header changed: method at 8 ("<H"), compressed size at 18 ("<L")."""
    out = bytearray(data)
    struct.pack_into(fmt, out, offset, value)
    return bytes(out)


def with_trailing_deflate_data(document_xml: bytes, trailer: bytes) -> bytes:
    """A one-entry zip whose entry's compressed data is a complete deflate
    stream followed by ``trailer``, with every header counting the trailer."""
    deflater = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = deflater.compress(document_xml) + deflater.flush() + trailer
    info = zipfile.ZipInfo("word/document.xml")
    info.compress_type = zipfile.ZIP_STORED
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, compressed)
    out = bytearray(buffer.getvalue())
    central = out.rindex(b"PK\x01\x02")
    struct.pack_into("<H", out, 8, zipfile.ZIP_DEFLATED)
    struct.pack_into("<H", out, central + 10, zipfile.ZIP_DEFLATED)
    struct.pack_into("<L", out, 22, len(document_xml))
    struct.pack_into("<L", out, central + 24, len(document_xml))
    return bytes(out)
