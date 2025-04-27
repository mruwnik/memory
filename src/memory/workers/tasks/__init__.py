"""
Import sub-modules so Celery can register their @app.task decorators.
"""
from memory.workers.tasks import text, photo, ocr, git, rss, docs   # noqa