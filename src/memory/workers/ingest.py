import logging

from memory.common import settings
from celery.schedules import crontab

from memory.common.celery_app import (
    app,
    CLEAN_ALL_COLLECTIONS,
    CLEANUP_EXPIRED_OAUTH_STATES,
    CLEANUP_OLD_CLAUDE_SESSIONS,
    CLEANUP_OLD_METRICS,
    CLEANUP_STUCK_MEETINGS,
    COLLECT_SYSTEM_METRICS,
    PROCESS_RAW_ITEMS,
    REINGEST_MISSING_CHUNKS,
    REFRESH_METRIC_SUMMARIES,
    SUMMARIZE_STALE_SESSIONS,
    SYNC_ALL_COMICS,
    SYNC_ALL_ARTICLE_FEEDS,
    TRACK_GIT_CHANGES,
    SYNC_LESSWRONG,
    RUN_SCHEDULED_TASKS,
    BACKUP_ALL,
    SYNC_ALL_GITHUB_REPOS,
    SYNC_ALL_GOOGLE_ACCOUNTS,
    SYNC_ALL_CALENDARS,
    SYNC_ALL_SLACK_WORKSPACES,
    VERIFY_ORPHANS,
)

logger = logging.getLogger(__name__)


app.conf.beat_schedule.update({
    "collect-system-metrics": {
        "task": COLLECT_SYSTEM_METRICS,
        "schedule": settings.METRICS_COLLECTION_INTERVAL,
    },
    "cleanup-old-metrics": {
        "task": CLEANUP_OLD_METRICS,
        "schedule": crontab(hour=str(settings.METRICS_CLEANUP_HOUR), minute="0"),
    },
    "refresh-metric-summaries": {
        "task": REFRESH_METRIC_SUMMARIES,
        "schedule": crontab(minute=str(settings.METRICS_SUMMARY_REFRESH_MINUTE)),
    },
    "clean-all-collections": {
        "task": CLEAN_ALL_COLLECTIONS,
        "schedule": settings.CLEAN_COLLECTION_INTERVAL,
    },
    "reingest-missing-chunks": {
        "task": REINGEST_MISSING_CHUNKS,
        "schedule": settings.CHUNK_REINGEST_INTERVAL,
    },
    "sync-mail-all": {
        "task": "memory.workers.tasks.email.sync_all_accounts",
        "schedule": settings.EMAIL_SYNC_INTERVAL,
    },
    "sync-all-comics": {
        "task": SYNC_ALL_COMICS,
        "schedule": settings.COMIC_SYNC_INTERVAL,
    },
    "sync-all-article-feeds": {
        "task": SYNC_ALL_ARTICLE_FEEDS,
        "schedule": settings.ARTICLE_FEED_SYNC_INTERVAL,
    },
    "sync-notes-changes": {
        "task": TRACK_GIT_CHANGES,
        "schedule": settings.NOTES_SYNC_INTERVAL,
    },
    "run-scheduled-tasks": {
        "task": RUN_SCHEDULED_TASKS,
        "schedule": settings.SCHEDULED_CALL_RUN_INTERVAL,
    },
    "backup-all": {
        "task": BACKUP_ALL,
        "schedule": settings.S3_BACKUP_INTERVAL,
    },
    "sync-github-repos": {
        "task": SYNC_ALL_GITHUB_REPOS,
        "schedule": settings.GITHUB_SYNC_INTERVAL,
    },
    "sync-google-drive": {
        "task": SYNC_ALL_GOOGLE_ACCOUNTS,
        "schedule": settings.GOOGLE_DRIVE_SYNC_INTERVAL,
    },
    "sync-calendars": {
        "task": SYNC_ALL_CALENDARS,
        "schedule": settings.CALENDAR_SYNC_INTERVAL,
    },
    "sync-slack-workspaces": {
        "task": SYNC_ALL_SLACK_WORKSPACES,
        "schedule": settings.SLACK_SYNC_INTERVAL,
    },
    "verify-orphans": {
        "task": VERIFY_ORPHANS,
        "schedule": settings.VERIFICATION_SYNC_INTERVAL,
    },
    "cleanup-old-claude-sessions": {
        "task": CLEANUP_OLD_CLAUDE_SESSIONS,
        "schedule": crontab(hour="3", minute="30"),  # Daily at 3:30 AM
    },
    "process-raw-items": {
        "task": PROCESS_RAW_ITEMS,
        "schedule": crontab(hour="4", minute="0"),  # Daily at 4 AM
    },
    "cleanup-expired-oauth-states": {
        "task": CLEANUP_EXPIRED_OAUTH_STATES,
        "schedule": crontab(minute="15"),  # Every hour at :15
    },
    "cleanup-stuck-meetings": {
        "task": CLEANUP_STUCK_MEETINGS,
        "schedule": crontab(minute="45"),  # Every hour at :45
    },
    "summarize-stale-sessions": {
        "task": SUMMARIZE_STALE_SESSIONS,
        "schedule": crontab(minute="30"),  # Every hour at :30
    },
})

if settings.LESSWRONG_SYNC_INTERVAL > 0:
    app.conf.beat_schedule["sync-lesswrong"] = {
        "task": SYNC_LESSWRONG,
        "schedule": settings.LESSWRONG_SYNC_INTERVAL,
    }
