"""hidden sensitivity tombstone level

Adds 'hidden' as an allowed sensitivity value on the two tables that can
produce hidden content today: source_item (all content types, via joined-table
inheritance) and email_accounts (the only API surface that can set it). The
search/visibility layer excludes 'hidden' for everyone, including admins.

Downgrade is lossy if any hidden rows exist — they would violate the 4-value
constraint. Operators must re-classify hidden content first.

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

FOUR = "sensitivity IN ('public', 'basic', 'internal', 'confidential')"
FIVE = "sensitivity IN ('public', 'basic', 'internal', 'confidential', 'hidden')"


def upgrade() -> None:
    op.drop_constraint("valid_sensitivity_level", "source_item", type_="check")
    op.create_check_constraint("valid_sensitivity_level", "source_item", FIVE)
    op.drop_constraint("valid_email_account_sensitivity", "email_accounts", type_="check")
    op.create_check_constraint("valid_email_account_sensitivity", "email_accounts", FIVE)


def downgrade() -> None:
    op.drop_constraint("valid_sensitivity_level", "source_item", type_="check")
    op.create_check_constraint("valid_sensitivity_level", "source_item", FOUR)
    op.drop_constraint("valid_email_account_sensitivity", "email_accounts", type_="check")
    op.create_check_constraint("valid_email_account_sensitivity", "email_accounts", FOUR)
