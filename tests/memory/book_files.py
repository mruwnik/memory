"""Builders for small book files that MuPDF opens as reflowable documents."""

import io
import struct
import zipfile

CONTAINER = (
    "<?xml version='1.0'?><container version='1.0' "
    "xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
    "<rootfile full-path='content.opf' media-type='application/oebps-package+xml'/>"
    "</rootfiles></container>"
)


def markup(paragraphs: int) -> bytes:
    return b"<html><body>" + b"<p>a</p>" * paragraphs + b"</body></html>"


def epub(chapter: bytes, repeats: int = 1) -> bytes:
    """An EPUB of one chapter that the spine lists `repeats` times."""
    opf = (
        "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>t</dc:title></metadata>"
        "<manifest><item id='c' href='c.xhtml' media-type='application/xhtml+xml'/></manifest>"
        "<spine>" + "<itemref idref='c'/>" * repeats + "</spine></package>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("content.opf", opf)
        archive.writestr("c.xhtml", chapter)
    return buffer.getvalue()


def mobi(text: bytes, name: bytes = b"book") -> bytes:
    """An uncompressed PalmDoc MOBI holding `text`."""
    size = 4096
    records = [text[i : i + size] for i in range(0, len(text), size)]
    palmdoc = struct.pack(">HHIHHHH", 1, 0, len(text), len(records), size, 0, 0)
    header0 = b"MOBI" + struct.pack(">III", 232, 2, 65001) + b"\0" * (232 - 16)
    all_records = [palmdoc + header0, *records]
    header = name.ljust(32, b"\0") + struct.pack(">HHIIIIII", 0, 0, 0, 0, 0, 0, 0, 0)
    header += b"BOOKMOBI" + struct.pack(">IIH", 0, 0, len(all_records))
    offset = len(header) + 8 * len(all_records) + 2
    table = b""
    for index, record in enumerate(all_records):
        table += struct.pack(">II", offset, 2 * index)
        offset += len(record)
    return header + table + b"\0\0" + b"".join(all_records)


def fb2(paragraphs: int) -> bytes:
    return (
        b"<?xml version='1.0'?><FictionBook xmlns='http://www.gribuser.ru/xml/fictionbook/2.0'>"
        b"<body><section>" + b"<p>a</p>" * paragraphs + b"</section></body></FictionBook>"
    )
