"""Measuring zip archives by their real inflated size before a parser reads them.

A zip entry's declared sizes and CRC are the sender's to choose, so entries
are measured by inflating their data, stopping just past the budget. Readers
also differ in where they look (central directory or local headers, first or
last archive in the file), so anything that lets two readers see different
content is refused.
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
END_RECORD = struct.Struct("<4s4H2LH")
END_RECORD_SIGNATURE = b"PK\x05\x06"
DATA_DESCRIPTOR_FLAG = 0x08
MARKUP_SUFFIXES = (".xml", ".rels")


def entry_data_offset(file: BinaryIO, info: zipfile.ZipInfo) -> int:
    """Where ``info``'s compressed data starts, after checking that its local
    header agrees with the central directory on the compression method and,
    unless sizes come in a data descriptor, on the sizes."""
    file.seek(info.header_offset)
    header = file.read(LOCAL_HEADER.size)
    if len(header) < LOCAL_HEADER.size or header[:4] != LOCAL_HEADER_SIGNATURE:
        raise ValueError("Archive has a damaged zip entry")
    _, _, flags, method, _, _, _, compressed, size, name_length, extra_length = (
        LOCAL_HEADER.unpack(header)
    )
    if method != info.compress_type:
        raise ValueError("Archive has a zip entry whose headers disagree")
    if not flags & DATA_DESCRIPTOR_FLAG and (compressed, size) != (
        info.compress_size,
        info.file_size,
    ):
        raise ValueError("Archive has a zip entry whose headers disagree")
    return info.header_offset + LOCAL_HEADER.size + name_length + extra_length


def stored_chunks(file: BinaryIO, info: zipfile.ZipInfo) -> Iterator[bytes]:
    """The compressed bytes of ``info``, read from after its local header."""
    file.seek(entry_data_offset(file, info))
    left = info.compress_size
    while left > 0:
        chunk = file.read(min(INFLATE_CHUNK, left))
        if not chunk:
            raise ValueError("Archive has a truncated zip entry")
        left -= len(chunk)
        yield chunk


def inflated_size(chunks: Iterator[bytes], method: int, budget: int) -> int:
    """The bytes ``chunks`` really inflate to, counting no further than just past
    ``budget``. Only stored and deflated entries are measured; a deflate stream
    must end exactly at the end of the entry's compressed data."""
    if method == zipfile.ZIP_STORED:
        return sum(len(chunk) for chunk in chunks)
    if method != zipfile.ZIP_DEFLATED:
        raise ValueError("Archive has a zip entry in an unsupported compression method")
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    size = 0
    for data in chunks:
        if inflater.eof:
            raise ValueError("Archive has data after a zip entry's deflate stream")
        while data and size <= budget:
            size += len(inflater.decompress(data, budget - size + 1))
            data = inflater.unconsumed_tail
        if size > budget:
            return size
    size += len(inflater.flush())
    if not inflater.eof or inflater.unused_data:
        raise ValueError("Archive has a zip entry whose deflate stream does not end with it")
    return size


def is_markup(info: zipfile.ZipInfo) -> bool:
    return info.filename.lower().endswith(MARKUP_SUFFIXES)


def check_archive_bounds(file: BinaryIO, archive: zipfile.ZipFile) -> None:
    """Refuse data before the archive (it must start at offset 0) or after
    its end record and comment."""
    entries = archive.infolist()
    file.seek(0)
    if file.read(len(LOCAL_HEADER_SIGNATURE)) != LOCAL_HEADER_SIGNATURE or not any(
        info.header_offset == 0 for info in entries
    ):
        raise ValueError("Document has data before its zip archive")
    end = file.seek(0, 2)
    file.seek(end - END_RECORD.size - len(archive.comment))
    record = file.read(END_RECORD.size)
    if len(record) < END_RECORD.size or record[:4] != END_RECORD_SIGNATURE:
        raise ValueError("Document has data after its zip archive")
    if END_RECORD.unpack(record)[-1] != len(archive.comment):
        raise ValueError("Document has data after its zip archive")


def check_office_zip(file: BinaryIO) -> None:
    """Raise ValueError unless ``file`` is a zipped office file (DOCX, XLSX,
    PPTX, ODT...) within the limits: at most MAX_OFFICE_ZIP_ENTRIES entries,
    markup entries inflating to at most MAX_OFFICE_MARKUP_BYTES, and all
    entries to at most MAX_OFFICE_INFLATED_BYTES. Content that isn't a zip,
    has data before or after the archive, or has an entry whose headers or
    inflated data disagree with its central directory record is refused.
    """
    try:
        archive = zipfile.ZipFile(file)
    except zipfile.BadZipFile as e:
        raise ValueError("Document is not a valid zip archive") from e
    with archive:
        entries = archive.infolist()
        if len(entries) > settings.MAX_OFFICE_ZIP_ENTRIES:
            raise ValueError(f"Document has more than {settings.MAX_OFFICE_ZIP_ENTRIES} entries")
        check_archive_bounds(file, archive)
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
