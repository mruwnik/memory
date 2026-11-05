import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from memory.common.db.models.discord import DiscordBot
from memory.common.db.models.slack import SlackApp

EVENT_ARRIVAL_RESOLUTION = timedelta(minutes=1)

# Process-local: the api runs one uvicorn worker (see docker/api/Dockerfile).
_seen_lock = threading.Lock()
_body_seen: dict[str, float] = {}  # body sha256 -> expiry, monotonic seconds


def register_body_seen(body_sha: str, ttl_seconds: int) -> bool:
    """True if this body hasn't been seen within ``ttl_seconds`` (and records it),
    False for a replay."""
    now = time.monotonic()
    with _seen_lock:
        for key in [k for k, expiry in _body_seen.items() if expiry <= now]:
            _body_seen.pop(key, None)
        if body_sha in _body_seen:
            return False
        _body_seen[body_sha] = now + ttl_seconds
        return True


def record_event_arrival(db: Session, row: SlackApp | DiscordBot) -> None:
    """Stamp last_event_at, at most once a minute so busy integrations don't write per event."""
    now = datetime.now(timezone.utc)
    if row.last_event_at and now - row.last_event_at < EVENT_ARRIVAL_RESOLUTION:
        return
    row.last_event_at = now
    db.commit()
