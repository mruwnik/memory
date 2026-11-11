"""A coding session's transcript file and the index of event uuids beside it.

The transcript is JSONL, one event per line. The index holds each event's
uuid, JSON-encoded, one per line, so a batch can be checked for events
already stored without parsing the transcript. Both are written only while
holding an exclusive flock on the transcript.
"""

import json
import pathlib
from typing import Iterable, Iterator, TextIO


def uuid_index_path(transcript_file: pathlib.Path) -> pathlib.Path:
    return transcript_file.with_name(f"{transcript_file.name}.uuids")


def uuid_line(uuid: str) -> str:
    """The index line for ``uuid``; JSON keeps a newline in it from splitting the line."""
    return json.dumps(uuid)


def index_text(uuids: Iterable[str]) -> str:
    return "".join(f"{uuid_line(uuid)}\n" for uuid in uuids)


def transcript_uuids(transcript: TextIO) -> Iterator[str]:
    """The uuid of each readable event line of ``transcript``, from its start."""
    transcript.seek(0)
    for line in transcript:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        uuid = data.get("uuid") if isinstance(data, dict) else None
        if isinstance(uuid, str):
            yield uuid


def _build_index_locked(transcript: TextIO, index_file: pathlib.Path) -> None:
    """Write the index of a transcript that has none. Caller must hold the
    transcript's lock."""
    partial = index_file.with_name(f"{index_file.name}.tmp")
    partial.write_text(index_text(transcript_uuids(transcript)))
    partial.replace(index_file)


def _stored_uuids_locked(
    transcript: TextIO, transcript_file: pathlib.Path, uuids: set[str]
) -> set[str]:
    """Which of ``uuids`` the transcript already holds, read from its index
    (built from the transcript once if missing). Caller must hold the
    transcript's lock."""
    index_file = uuid_index_path(transcript_file)
    if not index_file.exists():
        _build_index_locked(transcript, index_file)
    wanted = {uuid_line(uuid): uuid for uuid in uuids}
    with index_file.open() as index:
        return {wanted[line] for line in (raw.rstrip("\n") for raw in index) if line in wanted}


def _append_to_index_locked(transcript_file: pathlib.Path, uuids: Iterable[str]) -> None:
    """Record ``uuids`` as stored. Caller must hold the transcript's lock and
    have written their events to the transcript first, so a crash between the
    two leaves a resend duplicated rather than lost."""
    with uuid_index_path(transcript_file).open("a") as index:
        index.write(index_text(uuids))


def delete_transcript_files(transcript_file: pathlib.Path) -> None:
    """Remove a transcript and its index; either may already be gone."""
    transcript_file.unlink(missing_ok=True)
    uuid_index_path(transcript_file).unlink(missing_ok=True)
