"""File-type signatures read from a file's first bytes."""

# A PDF's header comment, and how much of a file's start is checked.
PDF_MAGIC = b"%PDF-"
PDF_MAGIC_WINDOW = 1024
# A PalmDB header's type and creator, at offset 60, for the books MuPDF opens:
# MOBI/AZW/AZW3 and plain PalmDoc.
PALMDB_TYPE_OFFSET = 60
MOBI_SIGNATURE = b"BOOKMOBI"
PALMDOC_SIGNATURE = b"TEXtREAd"
PALMDB_BOOK_SIGNATURES = (MOBI_SIGNATURE, PALMDOC_SIGNATURE)


# Bytes a PDF may start with before its header.
PDF_LEADING_BYTES = b"\0\t\n\f\r "
ZIP_LOCAL_HEADER = b"PK\x03\x04"
# Markup MuPDF would open as a reflowable document (FB2, HTML).
MARKUP_SIGNATURES = (b"<fictionbook", b"<html")


def has_pdf_magic(head: bytes) -> bool:
    """Whether ``head`` starts with the PDF header, after whitespace only."""
    return head[:PDF_MAGIC_WINDOW].lstrip(PDF_LEADING_BYTES).startswith(PDF_MAGIC)


def has_other_signature(head: bytes) -> bool:
    """Whether ``head`` also carries the signature of a format MuPDF lays out."""
    lowered = head[:PDF_MAGIC_WINDOW].lower()
    return (
        head.startswith(ZIP_LOCAL_HEADER)
        or palmdb_signature(head) in PALMDB_BOOK_SIGNATURES
        or any(tag in lowered for tag in MARKUP_SIGNATURES)
    )


def looks_like_pdf(head: bytes) -> bool:
    """A PDF header at the start and no other format's signature in ``head``."""
    return has_pdf_magic(head) and not has_other_signature(head)


def palmdb_signature(head: bytes) -> bytes:
    return head[PALMDB_TYPE_OFFSET : PALMDB_TYPE_OFFSET + 8]
