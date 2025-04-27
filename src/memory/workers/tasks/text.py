from memory.workers.celery_app import app

@app.task(name="memory.text.ping")
def ping():
    return "pong"