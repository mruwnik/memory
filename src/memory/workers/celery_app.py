from celery import Celery
from memory.common import settings

EMAIL_ROOT = "memory.workers.tasks.email"
FORUMS_ROOT = "memory.workers.tasks.forums"
BLOGS_ROOT = "memory.workers.tasks.blogs"
PHOTO_ROOT = "memory.workers.tasks.photo"
COMIC_ROOT = "memory.workers.tasks.comic"
EBOOK_ROOT = "memory.workers.tasks.ebook"
MAINTENANCE_ROOT = "memory.workers.tasks.maintenance"


def rabbit_url() -> str:
    return f"amqp://{settings.RABBITMQ_USER}:{settings.RABBITMQ_PASSWORD}@{settings.RABBITMQ_HOST}:5672//"


app = Celery(
    "memory",
    broker=rabbit_url(),
    backend=settings.CELERY_RESULT_BACKEND,
)


app.autodiscover_tasks(["memory.workers.tasks"])


app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        f"{EMAIL_ROOT}.*": {"queue": "email"},
        f"{PHOTO_ROOT}.*": {"queue": "photo_embed"},
        f"{COMIC_ROOT}.*": {"queue": "comic"},
        f"{EBOOK_ROOT}.*": {"queue": "ebooks"},
        f"{BLOGS_ROOT}.*": {"queue": "blogs"},
        f"{FORUMS_ROOT}.*": {"queue": "forums"},
        f"{MAINTENANCE_ROOT}.*": {"queue": "maintenance"},
    },
)


@app.on_after_configure.connect  # type: ignore
def ensure_qdrant_initialised(sender, **_):
    from memory.common import qdrant

    qdrant.setup_qdrant()
