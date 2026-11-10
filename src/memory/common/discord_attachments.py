"""Discord message attachments: how they are recorded, where their images are
stored, and how they read in a message's text."""

import hashlib
import mimetypes
import pathlib
import re
from typing import Any

from memory.common import paths, settings


def unsigned(url: str) -> str:
    """Discord's CDN signs attachment URLs with a query that changes over
    time, while the path (channel, attachment id, filename) stays the same.
    The result identifies the attachment; the CDN won't serve it unsigned."""
    return url.split("?")[0]


def is_image(content_type: str | None) -> bool:
    return (content_type or "").startswith("image/")


def image_path(url: str, message_id: int) -> pathlib.Path:
    """Where an image attachment of this message is stored, named by its unsigned URL."""
    path = unsigned(url)
    # SHA-256 truncated, matches parsers/html.py.
    url_hash = hashlib.sha256(path.encode()).hexdigest()[:32]
    ext = (pathlib.Path(path).suffix or ".jpg")[:10]
    return settings.DISCORD_STORAGE_DIR / str(message_id) / f"{url_hash}{ext}"


def stored_image_name(url: str, message_id: int) -> str:
    """The image's ``image_path`` as stored in ``DiscordMessage.images``."""
    return paths.to_db_filename(image_path(url, message_id))


def message_id_of(name: str) -> int | None:
    """The message id a stored image's directory is named after, None when it names none."""
    directory = pathlib.PurePath(name).parent.name
    if not re.fullmatch(r"[0-9]{1,19}", directory) or int(directory) >= 2**63:
        return None
    return int(directory)


def record(filename: str | None, content_type: str | None, size: int | None, url: str | None) -> dict[str, Any]:
    """An attachment as stored on its message. The URL is kept unsigned, so a
    re-read of the message records the same attachment the same way."""
    return {
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "url": url and unsigned(url),
    }


def described(attachment: dict[str, Any], message_id: int, stored: set[str]) -> dict[str, Any]:
    """An attachment as readers see it: ``file`` is the stored copy's path for
    ``core_fetch_file``, None when there is none (only images are downloaded)."""
    url = attachment.get("url")
    content_type = attachment.get("content_type")
    name = stored_image_name(url, message_id) if url and is_image(content_type) else None
    return {
        "filename": attachment.get("filename"),
        "content_type": content_type,
        "size": attachment.get("size"),
        "file": name if name in stored else None,
    }


def unrecorded(name: str) -> dict[str, Any]:
    """A stored image its message has no attachment record for."""
    return {
        "filename": pathlib.Path(name).name,
        "content_type": mimetypes.guess_type(name)[0],
        "size": None,
        "file": name,
    }


def attachment_files(
    attachments: list[dict[str, Any]] | None, images: list[str] | None, message_id: int
) -> list[dict[str, Any]]:
    """Every attachment of a message, each with filename, content_type, size and file."""
    stored = set(images or [])
    listed = [described(a, message_id, stored) for a in attachments or []]
    found = {a["file"] for a in listed}
    return listed + [unrecorded(name) for name in images or [] if name not in found]


def placeholder(attachment: dict[str, Any]) -> str:
    """``[image attachment: cat.png]``, so a message holding only attachments doesn't read as empty."""
    kind = "image attachment" if is_image(attachment.get("content_type")) else "attachment"
    return f"[{kind}: {attachment.get('filename') or 'unnamed'}]"
