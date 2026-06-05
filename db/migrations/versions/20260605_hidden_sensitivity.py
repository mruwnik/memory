"""hidden sensitivity tombstone level

Adds 'hidden' as an allowed sensitivity value on the two tables that can
produce hidden content today: source_item (all content types, via joined-table
inheritance) and email_accounts (the only API surface that can set it). The
search/visibility layer excludes 'hidden' for everyone, including admins.

The other 7 sensitivity check constraints (people, deadlines, ...) are left
4-valued on purpose: only email accounts can mint hidden content for now. To
extend hiding to another source type, add 'hidden' to its constraint the same
way.

Revision ID: 20260605_hidden_sensitivity
Revises: 549f6728ccfd
Create Date: 2026-06-05

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260605_hidden_sensitivity"
down_revision: Union[str, None] = "549f6728ccfd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, constraint name, allowed-value tuple)
_CONSTRAINTS = (
    ("source_item", "valid_sensitivity_level"),
    ("email_accounts", "valid_email_account_sensitivity"),
)
_FOUR = "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
_FIVE = "sensitivity IN ('public', 'basic', 'internal', 'confidential', 'hidden')"


def _recreate(condition: str) -> None:
    for table, name in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    _recreate(_FIVE)


def downgrade() -> None:
    # Downgrade is lossy if any hidden rows exist — they would violate the
    # 4-value constraint. Operators must re-classify hidden content first.
    _recreate(_FOUR)
