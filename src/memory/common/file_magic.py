"""File-type signatures read from a file's first bytes."""

# A PDF's header comment; readers accept it anywhere in the first 1024 bytes.
PDF_MAGIC = b"%PDF-"
PDF_MAGIC_WINDOW = 1024
# A PalmDB header's type and creator, at offset 60, for the books MuPDF opens:
# MOBI/AZW/AZW3 and plain PalmDoc.
PALMDB_TYPE_OFFSET = 60
MOBI_SIGNATURE = b"BOOKMOBI"
PALMDOC_SIGNATURE = b"TEXtREAd"
PALMDB_BOOK_SIGNATURES = (MOBI_SIGNATURE, PALMDOC_SIGNATURE)


def has_pdf_magic(head: bytes) -> bool:
    return PDF_MAGIC in head[:PDF_MAGIC_WINDOW]


def palmdb_signature(head: bytes) -> bytes:
    return head[PALMDB_TYPE_OFFSET : PALMDB_TYPE_OFFSET + 8]
