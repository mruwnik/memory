from memory.common.celery_app import app, build_beat_schedule

app.conf.beat_schedule.update(build_beat_schedule())
