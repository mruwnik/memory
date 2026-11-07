"""Subscriptions and deliveries get their own names: agent_triggers becomes
subscriptions, agent_trigger_matches becomes deliveries, and trigger_id on
agent_runs, deliveries and events becomes subscription_id. Indexes, constraints
and the id sequence follow, found by lookup since older instances may name them
differently; a name that doesn't follow the usual pattern is kept. Renames are
metadata only.

Default names left by earlier conversions ("trigger-<id>", "schedule-<task id>")
become "subscription-<id>". A delivery's status is one of the reasons the
pipeline records: the check is added NOT VALID and validated after the renames
commit, so deliveries stay writable while it scans them.

Revision ID: 20261217_subscriptions_contract
Revises: 20261216_raw_subscriptions
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261217_subscriptions_contract"
down_revision = "20261216_raw_subscriptions"
branch_labels = None
depends_on = None

TABLES = (("agent_triggers", "subscriptions"), ("agent_trigger_matches", "deliveries"))
COLUMNS = ("agent_runs", "deliveries", "events")
# Applied in order to every constraint, index and sequence name on the four tables.
NAMES = (
    (r"^agent_trigger_matches_", "deliveries_"),
    (r"^agent_triggers_", "subscriptions_"),
    (r"_trigger_id_", "_subscription_id_"),
    (r"^(agent_runs|deliveries)_trigger_(created|dedupe)_", r"\1_subscription_\2_"),
)
OLD_NAMES = (
    (r"^deliveries_", "agent_trigger_matches_"),
    (r"^subscriptions_", "agent_triggers_"),
    (r"_subscription_id_", "_trigger_id_"),
    (r"^(agent_runs|agent_trigger_matches)_subscription_(created|dedupe)_", r"\1_trigger_\2_"),
)
STATUSES = (
    "pending",
    "done",
    "failed",
    "expired",
    "skipped:inaccessible",
    "skipped:disabled",
    "skipped:posted",
    "skipped:coalesced",
    "skipped:shed:rate",
    "skipped:shed:budget",
    "skipped:shed:backlog",
)


def rename_objects(tables: tuple[str, ...], rules: tuple[tuple[str, str], ...]) -> str:
    """A DO block renaming the constraints, then the remaining indexes, then the
    serial sequences of `tables` by `rules`. A constraint's rename renames its index."""
    renamed = "n"
    for pattern, replacement in rules:
        renamed = f"regexp_replace({renamed}, '{pattern}', '{replacement}')"
    table_list = ", ".join(f"'{t}'" for t in tables)
    return f"""
DO $$
DECLARE t text; n text; seq regclass;
BEGIN
    FOREACH t IN ARRAY ARRAY[{table_list}] LOOP
        FOR n IN SELECT conname FROM pg_constraint WHERE conrelid = t::regclass LOOP
            IF {renamed} <> n THEN
                EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I', t, n, {renamed});
            END IF;
        END LOOP;
        FOR n IN
            SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = t::regclass
        LOOP
            IF {renamed} <> n THEN
                EXECUTE format('ALTER INDEX %I RENAME TO %I', n, {renamed});
            END IF;
        END LOOP;
        FOR seq IN
            SELECT d.objid::regclass FROM pg_depend d JOIN pg_class c ON c.oid = d.objid
            WHERE d.refobjid = t::regclass AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
        LOOP
            SELECT relname INTO n FROM pg_class WHERE oid = seq;
            IF {renamed} <> n THEN
                EXECUTE format('ALTER SEQUENCE %s RENAME TO %I', seq, {renamed});
            END IF;
        END LOOP;
    END LOOP;
END $$
"""


def rename_column(table: str, old: str, new: str) -> str:
    return f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()
               AND table_name = '{table}' AND column_name = '{old}') THEN
        ALTER TABLE {table} RENAME COLUMN {old} TO {new};
    END IF;
END $$
"""


def rename() -> None:
    """The upgrade's renames, in one transaction. Re-runnable, so an upgrade whose
    validation failed can be run again once the rows are fixed."""
    # Fail rather than hang behind the router and workers holding these tables.
    op.execute("SET LOCAL lock_timeout = '30s'")
    for old, new in TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {old} RENAME TO {new}")
    for table in COLUMNS:
        op.execute(rename_column(table, "trigger_id", "subscription_id"))
    op.execute(rename_objects(("subscriptions", "deliveries", "agent_runs", "events"), NAMES))
    op.execute(
        "UPDATE subscriptions s SET name = 'subscription-' || s.id "
        r"WHERE s.name ~ '^(trigger|schedule)-\d+$' AND NOT EXISTS (SELECT 1 FROM subscriptions o "
        "WHERE o.user_id = s.user_id AND o.name = 'subscription-' || s.id)"
    )
    statuses = ", ".join(f"'{s}'" for s in STATUSES)
    op.execute("ALTER TABLE deliveries DROP CONSTRAINT IF EXISTS deliveries_status_check")
    op.execute(
        "ALTER TABLE deliveries ADD CONSTRAINT deliveries_status_check "
        f"CHECK (status IN ({statuses})) NOT VALID"
    )
    op.execute("SET LOCAL lock_timeout = 0")


def validate() -> None:
    op.execute("ALTER TABLE deliveries VALIDATE CONSTRAINT deliveries_status_check")


def unrename() -> None:
    """The downgrade, in one transaction. Default names stay "subscription-<id>"."""
    op.execute("SET LOCAL lock_timeout = '30s'")
    op.execute("ALTER TABLE deliveries DROP CONSTRAINT IF EXISTS deliveries_status_check")
    op.execute(
        "ALTER TABLE deliveries ADD CONSTRAINT deliveries_status_check CHECK "
        "(status IN ('pending', 'done', 'failed', 'expired') OR status LIKE 'skipped:%')"
    )
    for table in COLUMNS:
        op.execute(f"ALTER TABLE {table} RENAME COLUMN subscription_id TO trigger_id")
    for old, new in TABLES:
        op.execute(f"ALTER TABLE {new} RENAME TO {old}")
    op.execute(
        rename_objects(("agent_triggers", "agent_trigger_matches", "agent_runs", "events"), OLD_NAMES)
    )
    op.execute("SET LOCAL lock_timeout = 0")


def upgrade() -> None:
    rename()
    with op.get_context().autocommit_block():
        validate()


def downgrade() -> None:
    unrename()
