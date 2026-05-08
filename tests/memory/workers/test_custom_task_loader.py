from unittest.mock import patch

from memory.common import settings
from memory.common.celery_app import app, custom_task_name, register_custom_beat
from memory.workers.custom_task_loader import load_custom_tasks


def test_custom_task_name_default():
    assert custom_task_name("deadline_check") == "custom_tasks.deadline_check.run"


def test_custom_task_name_custom_func():
    assert custom_task_name("reports", "generate") == "custom_tasks.reports.generate"


def test_register_custom_beat():
    name = register_custom_beat("weekly_report", 3600)
    assert name == "custom_tasks.weekly_report.run"
    entry = app.conf.beat_schedule["custom-tasks-weekly-report"]
    assert entry["task"] == name
    assert entry["schedule"] == 3600
    del app.conf.beat_schedule["custom-tasks-weekly-report"]


def test_register_custom_beat_custom_func():
    name = register_custom_beat("digest", 60, func_name="send")
    assert name == "custom_tasks.digest.send"
    assert "custom-tasks-digest" in app.conf.beat_schedule
    del app.conf.beat_schedule["custom-tasks-digest"]


def test_load_custom_tasks_no_dir_configured():
    with patch.object(settings, "CUSTOM_TASKS_DIR", None):
        assert load_custom_tasks() == []


def test_load_custom_tasks_dir_does_not_exist(tmp_path):
    nonexistent = str(tmp_path / "nope")
    with patch.object(settings, "CUSTOM_TASKS_DIR", nonexistent):
        assert load_custom_tasks() == []


def test_load_custom_tasks_empty_dir(tmp_path):
    with patch.object(settings, "CUSTOM_TASKS_DIR", str(tmp_path)):
        assert load_custom_tasks() == []


def test_load_custom_tasks_ignores_underscore_files(tmp_path):
    (tmp_path / "_disabled.py").write_text("raise RuntimeError('should not be loaded')")
    with patch.object(settings, "CUSTOM_TASKS_DIR", str(tmp_path)):
        assert load_custom_tasks() == []


def test_load_custom_tasks_loads_valid_file(tmp_path):
    (tmp_path / "my_task.py").write_text("LOADED = True\n")
    with patch.object(settings, "CUSTOM_TASKS_DIR", str(tmp_path)):
        result = load_custom_tasks()
    assert result == ["custom_tasks.my_task"]


def test_load_custom_tasks_broken_file_does_not_block_others(tmp_path):
    (tmp_path / "aaa_broken.py").write_text("raise RuntimeError('boom')")
    (tmp_path / "bbb_good.py").write_text("LOADED = True\n")
    with patch.object(settings, "CUSTOM_TASKS_DIR", str(tmp_path)):
        result = load_custom_tasks()
    assert result == ["custom_tasks.bbb_good"]


def test_load_custom_tasks_ignores_non_py_files(tmp_path):
    (tmp_path / "readme.txt").write_text("not a task")
    (tmp_path / "task.py").write_text("LOADED = True\n")
    with patch.object(settings, "CUSTOM_TASKS_DIR", str(tmp_path)):
        result = load_custom_tasks()
    assert result == ["custom_tasks.task"]


def test_celery_app_import_does_not_load_custom_tasks(tmp_path, monkeypatch):
    """Regression: importing memory.common.celery_app must NOT execute
    arbitrary user code from CUSTOM_TASKS_DIR.

    The loader used to run at module top level, which meant the API
    process (which imports memory.common.celery_app via jobs.py) would
    execute every custom task file's import-time code on every uvicorn
    boot — DB connections, network calls, monkey-patches, etc. The
    loading is now deferred to celery worker_init / beat_init signals
    that fire only on worker/beat startup.
    """
    # Place a custom task that flips a flag if it's loaded.
    canary = tmp_path / "noisy.py"
    canary.write_text(
        "import os\n"
        f"open({str(tmp_path / 'loaded')!r}, 'w').write('yes')\n"
    )
    monkeypatch.setattr(settings, "CUSTOM_TASKS_DIR", str(tmp_path))

    # Re-import the module to simulate API-process startup. The signal
    # handlers register, but worker_init / beat_init don't fire just
    # from the import itself.
    import importlib

    import memory.common.celery_app as celery_module

    importlib.reload(celery_module)

    # If the loader had run at import time, the canary file would
    # exist; with the deferred-to-signals change, it must not.
    assert not (tmp_path / "loaded").exists()
    assert celery_module._CUSTOM_TASKS_LOADED is False
