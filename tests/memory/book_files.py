"""Builders for small book files that MuPDF opens as reflowable documents."""

import io
import struct
import zipfile

CONTAINER = (
    "<?xml version='1.0'?><container version='1.0' "
    "xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
    "<rootfile full-path='{}' media-type='application/oebps-package+xml'/>"
    "</rootfiles></container>"
)


def markup(paragraphs: int) -> bytes:
    return b"<html><body>" + b"<p>a</p>" * paragraphs + b"</body></html>"


def epub_package(
    entries: dict[str, bytes],
    manifest: list[tuple[str, str]],
    spine: list[str],
    opf_path: str = "content.opf",
    container_path: str | None = None,
    media_types: dict[str, str] | None = None,
) -> bytes:
    """An EPUB of `entries` whose package lists `manifest` (id, href) and `spine` idrefs.
    Manifest items are XHTML unless `media_types` maps their id to another type."""
    types = media_types or {}
    opf = (
        "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>t</dc:title></metadata>"
        "<manifest>"
        + "".join(
            f"<item id='{item_id}' href='{href}' "
            f"media-type='{types.get(item_id, 'application/xhtml+xml')}'/>"
            for item_id, href in manifest
        )
        + "</manifest><spine>"
        + "".join(f"<itemref idref='{idref}'/>" for idref in spine)
        + "</spine></package>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER.format(container_path or opf_path))
        archive.writestr(opf_path, opf)
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def epub(chapter: bytes, repeats: int = 1) -> bytes:
    """An EPUB of one chapter that the spine lists `repeats` times."""
    return epub_package({"c.xhtml": chapter}, [("c", "c.xhtml")], ["c"] * repeats)


def palmdb(
    records: list[bytes],
    compression: int = 1,
    name: bytes = b"book",
    signature: bytes = b"BOOKMOBI",
    offsets: list[int] | None = None,
) -> bytes:
    """A PalmDB book whose record 0 says `records` are its text, compressed as `compression`."""
    text_length = sum(len(record) for record in records)
    palmdoc = struct.pack(">HHIHHHH", compression, 0, text_length, len(records), 4096, 0, 0)
    header0 = b"MOBI" + struct.pack(">III", 232, 2, 65001) + b"\0" * (232 - 16)
    all_records = [palmdoc + header0, *records]
    header = name.ljust(32, b"\0") + struct.pack(">HHIIIIII", 0, 0, 0, 0, 0, 0, 0, 0)
    header += signature + struct.pack(">IIH", 0, 0, len(all_records))
    offset = len(header) + 8 * len(all_records) + 2
    starts = []
    for record in all_records:
        starts.append(offset)
        offset += len(record)
    table = b"".join(
        struct.pack(">II", start, 2 * index)
        for index, start in enumerate(offsets or starts)
    )
    return header + table + b"\0\0" + b"".join(all_records)


def mobi(text: bytes, name: bytes = b"book", signature: bytes = b"BOOKMOBI") -> bytes:
    """An uncompressed PalmDoc MOBI holding `text`."""
    records = [text[i : i + 4096] for i in range(0, len(text), 4096)]
    return palmdb(records, name=name, signature=signature)


def palmdoc_record() -> bytes:
    """A PalmDoc-compressed record of `<p>a</p>` repeated, inflating about 5x past 4096 bytes."""
    literal = b"<p>a</p>"
    copy = struct.pack(">H", 0x8000 | (len(literal) << 3) | 7)
    return literal + copy * ((4096 - len(literal)) // 2)


def palmdoc_repeats(records: int) -> bytes:
    """A PalmDoc-compressed MOBI of `records` copies of palmdoc_record()."""
    return palmdb([palmdoc_record()] * records, compression=2)


def fb2(paragraphs: int) -> bytes:
    return (
        b"<?xml version='1.0'?><FictionBook xmlns='http://www.gribuser.ru/xml/fictionbook/2.0'>"
        b"<body><section>" + b"<p>a</p>" * paragraphs + b"</section></body></FictionBook>"
    )


def pdf_stream(data: bytes, dictionary: bytes = b"") -> bytes:
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (dictionary, len(data), data)


def nested_form_pdf(depth: int) -> bytes:
    """A valid one-page PDF whose page draws a form that draws ten of the next
    form, `depth` forms deep: about 2 KiB that MuPDF draws 10**depth times."""
    first_form = 6
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
        b" /Resources << /XObject << /F0 %d 0 R >> >> >>" % first_form,
        pdf_stream(b"/F0 Do"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 200 200] "
    for level in range(depth - 1):
        resources = b"/Resources << /XObject << /N %d 0 R >> >>" % (first_form + level + 1)
        objects.append(pdf_stream(b"/N Do " * 10, form + resources))
    text = b"BT /T1 8 Tf 10 10 Td (a) Tj ET"
    objects.append(pdf_stream(text, form + b"/Resources << /Font << /T1 5 0 R >> >>"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
