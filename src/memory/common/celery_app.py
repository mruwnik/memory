from celery import Celery
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
SCHEDULED_CALLS_ROOT = "memory.workers.tasks.scheduled_calls"
DISCORD_ROOT = "memory.workers.tasks.discord"
BACKUP_ROOT = "memory.workers.tasks.backup"
GITHUB_ROOT = "memory.workers.tasks.github"
PEOPLE_ROOT = "memory.workers.tasks.people"
PROACTIVE_ROOT = "memory.workers.tasks.proactive"
GOOGLE_ROOT = "memory.workers.tasks.google_drive"
CALENDAR_ROOT = "memory.workers.tasks.calendar"
MEETINGS_ROOT = "memory.workers.tasks.meetings"
METRICS_ROOT = "memory.workers.tasks.metrics"
ADD_DISCORD_MESSAGE = f"{DISCORD_ROOT}.add_discord_message"
EDIT_DISCORD_MESSAGE = f"{DISCORD_ROOT}.edit_discord_message"
PROCESS_DISCORD_MESSAGE = f"{DISCORD_ROOT}.process_discord_message"

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
SYNC_PHOTO = f"{PHOTO_ROOT}.sync_photo"
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
UPDATE_METADATA_FOR_SOURCE_ITEMS = (
    f"{MAINTENANCE_ROOT}.update_metadata_for_source_items"
)
UPDATE_METADATA_FOR_ITEM = f"{MAINTENANCE_ROOT}.update_metadata_for_item"
CLEANUP_EXPIRED_OAUTH_STATES = f"{MAINTENANCE_ROOT}.cleanup_expired_oauth_states"
CLEANUP_EXPIRED_SESSIONS = f"{MAINTENANCE_ROOT}.cleanup_expired_sessions"
SYNC_WEBPAGE = f"{BLOGS_ROOT}.sync_webpage"
SYNC_ARTICLE_FEED = f"{BLOGS_ROOT}.sync_article_feed"
SYNC_ALL_ARTICLE_FEEDS = f"{BLOGS_ROOT}.sync_all_article_feeds"
ADD_ARTICLE_FEED = f"{BLOGS_ROOT}.add_article_feed"
SYNC_WEBSITE_ARCHIVE = f"{BLOGS_ROOT}.sync_website_archive"

# Scheduled calls tasks
EXECUTE_SCHEDULED_CALL = f"{SCHEDULED_CALLS_ROOT}.execute_scheduled_call"
RUN_SCHEDULED_CALLS = f"{SCHEDULED_CALLS_ROOT}.run_scheduled_calls"

# Backup tasks
BACKUP_PATH = f"{BACKUP_ROOT}.backup_path"
BACKUP_ALL = f"{BACKUP_ROOT}.backup_all"

# GitHub tasks
SYNC_GITHUB_REPO = f"{GITHUB_ROOT}.sync_github_repo"
SYNC_ALL_GITHUB_REPOS = f"{GITHUB_ROOT}.sync_all_github_repos"
SYNC_GITHUB_ITEM = f"{GITHUB_ROOT}.sync_github_item"
SYNC_GITHUB_PROJECTS = f"{GITHUB_ROOT}.sync_github_projects"

# People tasks
SYNC_PERSON = f"{PEOPLE_ROOT}.sync_person"
UPDATE_PERSON = f"{PEOPLE_ROOT}.update_person"
SYNC_PROFILE_FROM_FILE = f"{PEOPLE_ROOT}.sync_profile_from_file"

# Proactive check-in tasks
EVALUATE_PROACTIVE_CHECKINS = f"{PROACTIVE_ROOT}.evaluate_proactive_checkins"
EXECUTE_PROACTIVE_CHECKIN = f"{PROACTIVE_ROOT}.execute_proactive_checkin"
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

# Metrics tasks
COLLECT_SYSTEM_METRICS = f"{METRICS_ROOT}.collect_system_metrics"
CLEANUP_OLD_METRICS = f"{METRICS_ROOT}.cleanup_old_metrics"
REFRESH_METRIC_SUMMARIES = f"{METRICS_ROOT}.refresh_metric_summaries"


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
        f"{EMAIL_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-email"},
        f"{FORUMS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-forums"},
        f"{MAINTENANCE_ROOT}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-maintenance"
        },
        f"{NOTES_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-notes"},
        f"{OBSERVATIONS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-notes"},
        f"{PHOTO_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-photos"},
        f"{SCHEDULED_CALLS_ROOT}.*": {
            "queue": f"{settings.CELERY_QUEUE_PREFIX}-scheduler"
        },
        f"{BACKUP_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-backup"},
        f"{GITHUB_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-github"},
        f"{PEOPLE_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-people"},
        f"{PROACTIVE_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-discord"},
        f"{GOOGLE_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-google"},
        f"{CALENDAR_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-calendar"},
        f"{MEETINGS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-meetings"},
        f"{METRICS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-maintenance"},
    },
)


@app.on_after_configure.connect  # type: ignore[attr-defined]
def ensure_qdrant_initialised(sender, **_):
    from memory.common import qdrant

    qdrant.setup_qdrant()
    # Note: load_servers() was removed as it's no longer needed
