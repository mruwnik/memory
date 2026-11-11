"""The Alembic chain stays linear."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).parents[4] / "db/migrations"


def test_alembic_has_exactly_one_head():
    config = Config(str(MIGRATIONS / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
