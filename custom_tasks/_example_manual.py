"""Example custom task template — manual/on-demand only (no beat schedule).

Use this pattern for tasks you only trigger manually or from other code:
    celery -A memory.workers.ingest call custom_tasks.my_report.run

Files starting with '_' are ignored by the loader, so this won't be loaded.
To enable, copy to a file without the '_' prefix.

See also: _example.py for a periodic task with a beat schedule.
"""

from memory.common.celery_app import app, custom_task_name
from memory.common.db.connection import make_session

# custom_task_name just builds the name string: "custom_tasks._example_manual.run"
# No beat schedule is registered — this task must be called explicitly.
TASK_NAME = custom_task_name("_example_manual")


@app.task(name=TASK_NAME)
def run():
    with make_session() as session:
        # Full access to DB, Discord, GitHub clients, etc.
        pass
    return {"status": "success"}
