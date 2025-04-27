from memory.workers.celery_app import app


@app.task(name="kb.text.ping")
def ping():
    return "pong"