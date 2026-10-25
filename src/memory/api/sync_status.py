from pydantic import BaseModel

from memory.common.db.models.sync_status import SyncStatusMixin, redact_secrets


class SyncStatusResponse(BaseModel):
    sync_error: str | None
    sync_failing_since: str | None


def sync_status_fields(source: SyncStatusMixin) -> dict[str, str | None]:
    # Redacted again on read: rows stored before redaction existed, or by writes
    # that bypass record_sync, still reach users.
    since = source.sync_failing_since
    return {
        "sync_error": source.sync_error and redact_secrets(source.sync_error),
        "sync_failing_since": since.isoformat() if since else None,
    }
