"""Measuring zip archives by their real inflated size before a parser reads them.

A zip entry's declared sizes and CRC are the sender's to choose, so entries
are measured by inflating their data, stopping just past the budget.
"""

import struct
import zipfile
import zlib
from typing import BinaryIO, Iterator

from memory.common import settings

# Bytes read per step while measuring an entry.
INFLATE_CHUNK = 64 * 1024
LOCAL_HEADER = struct.Struct("<4s5H3L2H")
LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"
MARKUP_SUFFIXES = (".xml", ".rels")


def stored_chunks(file: BinaryIO, info: zipfile.ZipInfo) -> Iterator[bytes]:
    """The compressed bytes of ``info``, read from after its local header."""
    file.seek(info.header_offset)
    header = file.read(LOCAL_HEADER.size)
    if len(header) < LOCAL_HEADER.size or header[:4] != LOCAL_HEADER_SIGNATURE:
        raise ValueError("Archive has a damaged zip entry")
    *_, name_length, extra_length = LOCAL_HEADER.unpack(header)
    file.seek(name_length + extra_length, 1)
    left = info.compress_size
    while left > 0:
        chunk = file.read(min(INFLATE_CHUNK, left))
        if not chunk:
            raise ValueError("Archive has a truncated zip entry")
        left -= len(chunk)
        yield chunk


def inflated_size(chunks: Iterator[bytes], method: int, budget: int) -> int:
    """The bytes ``chunks`` really inflate to, counting no further than just past
    ``budget``. Only stored and deflated entries are measured."""
    if method == zipfile.ZIP_STORED:
        return sum(len(chunk) for chunk in chunks)
    if method != zipfile.ZIP_DEFLATED:
        raise ValueError("Archive has a zip entry in an unsupported compression method")
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    size = 0
    for data in chunks:
        while data and size <= budget:
            size += len(inflater.decompress(data, budget - size + 1))
            data = inflater.unconsumed_tail
        if size > budget or inflater.eof:
            return size
    return size + len(inflater.flush())


def is_markup(info: zipfile.ZipInfo) -> bool:
    return info.filename.lower().endswith(MARKUP_SUFFIXES)


def check_office_zip(file: BinaryIO) -> None:
    """Raise ValueError for a zipped office file (DOCX, XLSX, PPTX, ODT...) with
    more than MAX_OFFICE_ZIP_ENTRIES entries, whose markup entries inflate past
    MAX_OFFICE_MARKUP_BYTES, or whose entries inflate past
    MAX_OFFICE_INFLATED_BYTES in total. An entry that inflates to other than
    its declared size is refused too, since readers disagree on which to trust.
    Content that isn't a zip (a legacy .doc) passes unmeasured.
    """
    if not zipfile.is_zipfile(file):
        return
    try:
        archive = zipfile.ZipFile(file)
    except zipfile.BadZipFile as e:
        raise ValueError("Document is not a valid zip archive") from e
    with archive:
        entries = archive.infolist()
        if len(entries) > settings.MAX_OFFICE_ZIP_ENTRIES:
            raise ValueError(f"Document has more than {settings.MAX_OFFICE_ZIP_ENTRIES} entries")
        total, markup = 0, 0
        for info in entries:
            budget = settings.MAX_OFFICE_INFLATED_BYTES - total
            if is_markup(info):
                budget = min(budget, settings.MAX_OFFICE_MARKUP_BYTES - markup)
            try:
                size = inflated_size(stored_chunks(file, info), info.compress_type, budget)
            except zlib.error as e:
                raise ValueError("Document has a zip entry that does not inflate") from e
            if size > budget:
                raise ValueError("Document inflates past the office file size limits")
            if size != info.file_size:
                raise ValueError("Document has a zip entry whose size does not match its header")
            total += size
            markup += size if is_markup(info) else 0
