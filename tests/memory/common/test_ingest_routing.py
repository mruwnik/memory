import io
import zipfile

import pytest

from memory.common import ingest_routing as ir
from memory.common import settings


def _minimal_epub() -> bytes:
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    z.writestr(
        zipfile.ZipInfo("mimetype"),
        "application/epub+zip",
        compress_type=zipfile.ZIP_STORED,
    )
    z.writestr(
        "META-INF/container.xml",
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>',
    )
    z.writestr(
        "content.opf",
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="id"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x'
        "</dc:identifier><dc:title>T</dc:title><dc:language>en</dc:language>"
        '</metadata><manifest><item id="c1" href="c1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest><spine>'
        '<itemref idref="c1"/></spine></package>',
    )
    z.writestr(
        "c1.xhtml",
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<body><p>hello book</p></body></html>",
    )
    z.close()
    return buf.getvalue()


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _pdf() -> bytes:
    with open("tests/data/sample.pdf", "rb") as f:
        return f.read()


def test_epub_routes_to_book():
    spec = ir.detect_bucket("application/epub+zip", _minimal_epub())
    assert spec.name == "book"
    assert spec.task_name.endswith("sync_book")
    assert spec.storage_dir == settings.EBOOK_STORAGE_DIR


def test_png_routes_to_image_even_if_mislabeled():
    # Declared as a PDF, but the bytes are a PNG -> content wins -> image.
    spec = ir.detect_bucket("application/pdf", _png())
    assert spec.name == "image"
    assert spec.task_name.endswith("sync_photo")


def test_pdf_routes_to_misc():
    spec = ir.detect_bucket("application/pdf", _pdf())
    assert spec.name == "misc"
    assert spec.dedupe_field == "sha256"


def test_pdf_mislabeled_as_epub_still_misc():
    # The misclassification this design targets: a PDF the caller called epub.
    spec = ir.detect_bucket("application/epub+zip", _pdf())
    assert spec.name == "misc"


@pytest.mark.parametrize(
    "content",
    [
        b"just some plain text, long enough\n" * 3,
        b'{"a": 1, "b": [2, 3]}',
        b"PK\x03\x04" + b"\x00" * 40,  # a non-epub zip
        b"\x00\x01\x02\x03 arbitrary binary",
    ],
)
def test_unrecognized_routes_to_misc(content):
    spec = ir.detect_bucket("application/octet-stream", content)
    assert spec.name == "misc"


def test_declared_image_type_routes_to_image():
    spec = ir.detect_bucket("image/png", _png())
    assert spec.name == "image"


def test_max_ingest_bytes_is_largest_cap():
    assert ir.max_ingest_bytes() == max(
        settings.MAX_BOOK_UPLOAD_BYTES,
        settings.MAX_PHOTO_UPLOAD_BYTES,
        settings.MAX_MISC_UPLOAD_BYTES,
    )
