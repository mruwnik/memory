import os
from celery import Celery
from memory.common import settings

def rabbit_url() -> str:
    user = os.getenv("RABBITMQ_USER", "guest")
    password = os.getenv("RABBITMQ_PASSWORD", "guest")
    return f"amqp://{user}:{password}@rabbitmq:5672//"


app = Celery(
    "memory",
    broker=rabbit_url(),
    backend=os.getenv("CELERY_RESULT_BACKEND", f"db+{settings.DB_URL}")
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
        "memory.workers.tasks.ocr.*": {"queue": "low_ocr"},
        "memory.workers.tasks.git.*": {"queue": "git_summary"},
        "memory.workers.tasks.rss.*": {"queue": "rss"},
        "memory.workers.tasks.docs.*": {"queue": "docs"},
    },
)
