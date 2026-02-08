from celery import Celery
from celery.schedules import crontab
from kombu.utils.url import safequote

from memory.common import settings

EMAIL_ROOT = "memory.workers.tasks.email"
FORUMS_ROOT = "memory.workers.tasks.forums"
BLOGS_ROOT = "memory.workers.tasks.blogs"
PHOTO_ROOT = "memory.workers.tasks.photo"
COMIC_ROOT = "memory.workers.tasks.comic"
EBOOK_ROOT = "memory.workers.tasks.ebook"
MAINTENANCE_ROOT = "memory.workers.tasks.maintenance"
NOTES_ROOT = "memory.workers.tasks.notes"
OBSERVATIONS_ROOT = "memory.workers.tasks.observations"
SCHEDULED_TASKS_ROOT = "memory.workers.tasks.scheduled_tasks"
DISCORD_ROOT = "memory.workers.tasks.discord"
SLACK_ROOT = "memory.workers.tasks.slack"
BACKUP_ROOT = "memory.workers.tasks.backup"
GITHUB_ROOT = "memory.workers.tasks.github"
PEOPLE_ROOT = "memory.workers.tasks.people"
GOOGLE_ROOT = "memory.workers.tasks.google_drive"
CALENDAR_ROOT = "memory.workers.tasks.calendar"
MEETINGS_ROOT = "memory.workers.tasks.meetings"
REPORTS_ROOT = "memory.workers.tasks.reports"
METRICS_ROOT = "memory.workers.tasks.metrics"
VERIFICATION_ROOT = "memory.workers.tasks.verification"
ADD_DISCORD_MESSAGE = f"{DISCORD_ROOT}.add_discord_message"
EDIT_DISCORD_MESSAGE = f"{DISCORD_ROOT}.edit_discord_message"
UPDATE_REACTIONS = f"{DISCORD_ROOT}.update_reactions"

# Slack tasks
SYNC_ALL_SLACK_WORKSPACES = f"{SLACK_ROOT}.sync_all_slack_workspaces"
SYNC_SLACK_WORKSPACE = f"{SLACK_ROOT}.sync_slack_workspace"
SYNC_SLACK_CHANNEL = f"{SLACK_ROOT}.sync_slack_channel"
ADD_SLACK_MESSAGE = f"{SLACK_ROOT}.add_slack_message"

SYNC_NOTES = f"{NOTES_ROOT}.sync_notes"
SYNC_NOTE = f"{NOTES_ROOT}.sync_note"
SETUP_GIT_NOTES = f"{NOTES_ROOT}.setup_git_notes"
TRACK_GIT_CHANGES = f"{NOTES_ROOT}.track_git_changes"
SYNC_OBSERVATION = f"{OBSERVATIONS_ROOT}.sync_observation"
SYNC_ALL_COMICS = f"{COMIC_ROOT}.sync_all_comics"
SYNC_SMBC = f"{COMIC_ROOT}.sync_smbc"
SYNC_XKCD = f"{COMIC_ROOT}.sync_xkcd"
SYNC_COMIC = f"{COMIC_ROOT}.sync_comic"
SYNC_BOOK = f"{EBOOK_ROOT}.sync_book"
REPROCESS_BOOK = f"{EBOOK_ROOT}.reprocess_book"
SYNC_PHOTO = f"{PHOTO_ROOT}.sync_photo"
REPROCESS_PHOTO = f"{PHOTO_ROOT}.reprocess_photo"
PROCESS_EMAIL = f"{EMAIL_ROOT}.process_message"
SYNC_ACCOUNT = f"{EMAIL_ROOT}.sync_account"
SYNC_ALL_ACCOUNTS = f"{EMAIL_ROOT}.sync_all_accounts"
SYNC_LESSWRONG = f"{FORUMS_ROOT}.sync_lesswrong"
SYNC_LESSWRONG_POST = f"{FORUMS_ROOT}.sync_lesswrong_post"
CLEAN_ALL_COLLECTIONS = f"{MAINTENANCE_ROOT}.clean_all_collections"
CLEAN_COLLECTION = f"{MAINTENANCE_ROOT}.clean_collection"
REINGEST_MISSING_CHUNKS = f"{MAINTENANCE_ROOT}.reingest_missing_chunks"
REINGEST_CHUNK = f"{MAINTENANCE_ROOT}.reingest_chunk"
REINGEST_ITEM = f"{MAINTENANCE_ROOT}.reingest_item"
REINGEST_EMPTY_SOURCE_ITEMS = f"{MAINTENANCE_ROOT}.reingest_empty_source_items"
REINGEST_ALL_EMPTY_SOURCE_ITEMS = f"{MAINTENANCE_ROOT}.reingest_all_empty_source_items"
PROCESS_RAW_ITEMS = f"{MAINTENANCE_ROOT}.process_raw_items"
PROCESS_RAW_ITEM = f"{MAINTENANCE_ROOT}.process_raw_item"
UPDATE_METADATA_FOR_SOURCE_ITEMS = (
    f"{MAINTENANCE_ROOT}.update_metadata_for_source_items"
)
UPDATE_METADATA_FOR_ITEM = f"{MAINTENANCE_ROOT}.update_metadata_for_item"
CLEANUP_EXPIRED_OAUTH_STATES = f"{MAINTENANCE_ROOT}.cleanup_expired_oauth_states"
CLEANUP_EXPIRED_SESSIONS = f"{MAINTENANCE_ROOT}.cleanup_expired_sessions"
CLEANUP_OLD_CLAUDE_SESSIONS = f"{MAINTENANCE_ROOT}.cleanup_old_claude_sessions"
SYNC_WEBPAGE = f"{BLOGS_ROOT}.sync_webpage"
SYNC_ARTICLE_FEED = f"{BLOGS_ROOT}.sync_article_feed"
SYNC_ALL_ARTICLE_FEEDS = f"{BLOGS_ROOT}.sync_all_article_feeds"
ADD_ARTICLE_FEED = f"{BLOGS_ROOT}.add_article_feed"
SYNC_WEBSITE_ARCHIVE = f"{BLOGS_ROOT}.sync_website_archive"

# Scheduled tasks
EXECUTE_SCHEDULED_TASK = f"{SCHEDULED_TASKS_ROOT}.execute_scheduled_task"
RUN_SCHEDULED_TASKS = f"{SCHEDULED_TASKS_ROOT}.run_scheduled_tasks"

# Backup tasks
BACKUP_PATH = f"{BACKUP_ROOT}.backup_path"
BACKUP_ALL = f"{BACKUP_ROOT}.backup_all"

# GitHub tasks
SYNC_GITHUB_REPO = f"{GITHUB_ROOT}.sync_github_repo"
SYNC_ALL_GITHUB_REPOS = f"{GITHUB_ROOT}.sync_all_github_repos"
SYNC_GITHUB_ITEM = f"{GITHUB_ROOT}.sync_github_item"
SYNC_GITHUB_PROJECTS = f"{GITHUB_ROOT}.sync_github_projects"

# People tasks
SYNC_PERSON_TIDBIT = f"{PEOPLE_ROOT}.sync_person_tidbit"

# Google Drive tasks
SYNC_GOOGLE_FOLDER = f"{GOOGLE_ROOT}.sync_google_folder"
SYNC_GOOGLE_DOC = f"{GOOGLE_ROOT}.sync_google_doc"
SYNC_ALL_GOOGLE_ACCOUNTS = f"{GOOGLE_ROOT}.sync_all_google_accounts"

# Calendar tasks
SYNC_CALENDAR_ACCOUNT = f"{CALENDAR_ROOT}.sync_calendar_account"
SYNC_CALENDAR_EVENT = f"{CALENDAR_ROOT}.sync_calendar_event"
SYNC_ALL_CALENDARS = f"{CALENDAR_ROOT}.sync_all_calendars"

# Meeting tasks
PROCESS_MEETING = f"{MEETINGS_ROOT}.process_meeting"
REPROCESS_MEETING = f"{MEETINGS_ROOT}.reprocess_meeting"
CLEANUP_STUCK_MEETINGS = f"{MEETINGS_ROOT}.cleanup_stuck_meetings"

# Report tasks
SYNC_REPORT = f"{REPORTS_ROOT}.sync_report"

# Metrics tasks
COLLECT_SYSTEM_METRICS = f"{METRICS_ROOT}.collect_system_metrics"
CLEANUP_OLD_METRICS = f"{METRICS_ROOT}.cleanup_old_metrics"
REFRESH_METRIC_SUMMARIES = f"{METRICS_ROOT}.refresh_metric_summaries"

# Verification tasks
VERIFY_ORPHANS = f"{VERIFICATION_ROOT}.verify_orphans"
VERIFY_SOURCE_BATCH = f"{VERIFICATION_ROOT}.verify_source_batch"

# Access control tasks
UPDATE_SOURCE_ACCESS_CONTROL = f"{MAINTENANCE_ROOT}.update_source_access_control"

# Session tasks
SESSIONS_ROOT = "memory.workers.tasks.sessions"
SUMMARIZE_SESSION = f"{SESSIONS_ROOT}.summarize_session"
SUMMARIZE_STALE_SESSIONS = f"{SESSIONS_ROOT}.summarize_stale_sessions"

# Custom tasks (deployment-specific, loaded from CUSTOM_TASKS_DIR)
CUSTOM_TASKS_PREFIX = "custom_tasks"


def custom_task_name(filename_stem: str, func_name: str = "run") -> str:
    """Build a canonical Celery task name for a custom task file.

    >>> custom_task_name("deadline_check")
    'custom_tasks.deadline_check.run'
    """
    return f"{CUSTOM_TASKS_PREFIX}.{filename_stem}.{func_name}"


def register_custom_beat(
    filename_stem: str,
    schedule: float | crontab,
    func_name: str = "run",
) -> str:
    """Register a custom task in the Celery beat schedule.

    Returns the task name for use with @app.task(name=...).

    >>> name = register_custom_beat("deadline_check", crontab(hour=9, minute=0))
    """
    task_name = custom_task_name(filename_stem, func_name)
    beat_key = f"custom-tasks-{filename_stem.replace('_', '-')}"
    app.conf.beat_schedule[beat_key] = {
        "task": task_name,
        "schedule": schedule,
    }
    return task_name


def get_broker_url() -> str:
    protocol = settings.CELERY_BROKER_TYPE
    user = safequote(settings.CELERY_BROKER_USER)
    password = safequote(settings.CELERY_BROKER_PASSWORD or "")
    host = settings.CELERY_BROKER_HOST

    if password:
        url = f"{protocol}://{user}:{password}@{host}"
    else:
        url = f"{protocol}://{host}"

    if protocol == "redis":
        url += f"/{settings.REDIS_DB}"
    return url


app = Celery(
    settings.APP_NAME,
    broker=get_broker_url(),
    backend=settings.CELERY_RESULT_BACKEND,
)

app.autodiscover_tasks(["memory.workers.tasks"])


app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Default retry configuration for transient failures
    task_autoretry_for=(Exception,),
    task_retry_kwargs={"max_retries": 3},
    task_retry_backoff=True,
    task_retry_backoff_max=600,  # Max 10 minutes between retries
    task_retry_jitter=True,
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3000,  # 50 minute soft limit
    task_routes={
        f"{EBOOK_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-ebooks"},
        f"{BLOGS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-blogs"},
        f"{COMIC_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-comic"},
        f"{DISCORD_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-discord"},
        f"{SLACK_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-slack"},
        f"{EMAIL_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-email"},
        f"{FORUMS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-forums"},
        f"{MAINTENANCE_ROOT}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-maintenance"
        },
        f"{NOTES_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-notes"},
        f"{OBSERVATIONS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-notes"},
        f"{PHOTO_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-photos"},
        f"{SCHEDULED_TASKS_ROOT}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-scheduler"
        },
        f"{BACKUP_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-backup"},
        f"{GITHUB_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-github"},
        f"{PEOPLE_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-people"},
        f"{GOOGLE_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-google"},
        f"{CALENDAR_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-calendar"},
        f"{MEETINGS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-meetings"},
        f"{REPORTS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-reports"},
        f"{METRICS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-maintenance"},
        f"{VERIFICATION_ROOT}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-maintenance"
        },
        f"{SESSIONS_ROOT}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-maintenance"
        },
        f"{CUSTOM_TASKS_PREFIX}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-custom"
        },
    },
)


def build_beat_schedule() -> dict:
    """Build the beat schedule dict. Used by the ingest worker to register
    with celery, and by the API to expose schedule metadata."""
    schedule = {
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
            "task": SYNC_ALL_ACCOUNTS,
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
            "schedule": crontab(hour="3", minute="30"),
        },
        "process-raw-items": {
            "task": PROCESS_RAW_ITEMS,
            "schedule": crontab(hour="4", minute="0"),
        },
        "cleanup-expired-oauth-states": {
            "task": CLEANUP_EXPIRED_OAUTH_STATES,
            "schedule": crontab(minute="15"),
        },
        "cleanup-stuck-meetings": {
            "task": CLEANUP_STUCK_MEETINGS,
            "schedule": crontab(minute="45"),
        },
        "summarize-stale-sessions": {
            "task": SUMMARIZE_STALE_SESSIONS,
            "schedule": crontab(minute="30"),
        },
    }
    if settings.LESSWRONG_SYNC_INTERVAL > 0:
        schedule["sync-lesswrong"] = {
            "task": SYNC_LESSWRONG,
            "schedule": settings.LESSWRONG_SYNC_INTERVAL,
        }
    return schedule


@app.on_after_configure.connect  # type: ignore[attr-defined]
def setup_on_configure(sender, **_):
    from memory.common import qdrant

    qdrant.setup_qdrant()


# Load custom tasks at import time so they're registered before the worker starts.
# This must be after app and conf are fully set up.
from memory.workers.custom_task_loader import load_custom_tasks  # noqa: E402

load_custom_tasks()
