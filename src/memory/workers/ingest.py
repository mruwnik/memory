
from memory.workers.celery_app import app
from memory.common import settings


app.conf.beat_schedule = {
    'sync-mail-all': {
        'task': 'memory.workers.tasks.email.sync_all_accounts',
        'schedule': settings.EMAIL_SYNC_INTERVAL,
    },
}