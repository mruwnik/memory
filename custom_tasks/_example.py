"""Example custom task template — periodic task with beat schedule.

Files in CUSTOM_TASKS_DIR are auto-loaded at worker startup.
Convention:
  - Files starting with '_' are ignored (use for templates/disabled tasks)
  - Tasks are routed to the 'custom' queue automatically

To enable, copy to a file without the '_' prefix:
    cp _example.py deadline_check.py

See also: _example_manual.py for a task without a beat schedule (manual/on-demand only).
"""

from celery.schedules import crontab

from memory.common.celery_app import app, register_custom_beat
from memory.common.db.connection import make_session

# register_custom_beat does two things:
#   1. Builds the Celery task name: "custom_tasks._example.run"
#      (from the filename stem + default func_name="run")
#   2. Adds a beat_schedule entry so Celery Beat runs it on the given schedule
# It returns the task name string, which you pass to @app.task(name=...).
TASK_NAME = register_custom_beat(
    "_example",                                     # filename stem (must match this file)
    crontab(hour=9, minute=0, day_of_week="mon-fri"),  # when to run
)


# The function name doesn't matter to Celery — the `name` kwarg is what counts.
# "run" is the convention, but you can use anything.
@app.task(name=TASK_NAME)
def run():
    with make_session() as session:
        # Full access to DB, Discord, GitHub clients, etc.
        pass
    return {"status": "success"}
