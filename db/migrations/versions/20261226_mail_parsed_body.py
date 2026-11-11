"""Store an email's extracted body.

Readers (search results, listings, subscriptions) used the body re-extracted
from the raw message on every read. Mail stored before this keeps a NULL
``parsed_body`` and is still extracted on read.

Revision ID: 20261226_mail_parsed_body
Revises: 20261225_null_gdoc_filename
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261226_mail_parsed_body"
down_revision: Union[str, None] = "20261225_null_gdoc_filename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mail_message", sa.Column("parsed_body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mail_message", "parsed_body")
