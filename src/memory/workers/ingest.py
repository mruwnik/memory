from celery.schedules import schedule
from memory.workers.tasks.email import SYNC_ALL_ACCOUNTS
from memory.common import settings
from memory.workers.celery_app import app


@app.on_after_configure.connect
def register_mail_schedules(sender, **_):
    sender.add_periodic_task(
        schedule=schedule(settings.EMAIL_SYNC_INTERVAL),
        sig=app.signature(SYNC_ALL_ACCOUNTS),
        name="sync-mail-all",
        options={"queue": "email"},
    )
