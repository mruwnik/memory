import importlib.util
import logging
import pathlib

from memory.common import settings

logger = logging.getLogger(__name__)


def load_custom_tasks() -> list[str]:
    """Load deployment-specific Celery tasks from CUSTOM_TASKS_DIR.

    Each .py file (not starting with '_') is imported, which triggers
    @app.task registration and beat_schedule additions within the file.

    Returns the list of module names that were successfully loaded.
    """
    if not settings.CUSTOM_TASKS_DIR:
        return []

    tasks_dir = pathlib.Path(settings.CUSTOM_TASKS_DIR)
    if not tasks_dir.is_dir():
        logger.warning("CUSTOM_TASKS_DIR %s does not exist or is not a directory", tasks_dir)
        return []

    task_files = sorted(
        f for f in tasks_dir.glob("*.py") if not f.name.startswith("_")
    )
    if not task_files:
        logger.info("No custom tasks found in %s", tasks_dir)
        return []

    loaded = []
    for task_file in task_files:
        module_name = f"custom_tasks.{task_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, task_file)
            if spec is None or spec.loader is None:
                logger.error("Failed to create module spec for %s", task_file)
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(module_name)
            logger.info("Loaded custom task: %s", module_name)
        except Exception:
            logger.exception("Failed to load custom task %s", task_file)

    logger.info("Loaded %d custom task(s) from %s", len(loaded), tasks_dir)
    return loaded
