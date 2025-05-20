from celery import Celery
from memory.common import settings


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
        "memory.workers.tasks.text.*": {"queue": "medium_embed"},
        "memory.workers.tasks.email.*": {"queue": "email"},
        "memory.workers.tasks.photo.*": {"queue": "photo_embed"},
        "memory.workers.tasks.comic.*": {"queue": "comic"},
        "memory.workers.tasks.docs.*": {"queue": "docs"},
        "memory.workers.tasks.maintenance.*": {"queue": "maintenance"},
    },
)


@app.on_after_configure.connect  # type: ignore
def ensure_qdrant_initialised(sender, **_):
    from memory.common import qdrant

    qdrant.setup_qdrant()
