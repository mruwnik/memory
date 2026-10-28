"""Drop sender tags from email attachments.

Ingest tagged every attachment with its message's raw From header. Removes
tags equal to the parent message's sender, and any tag that looks like a raw
address header (``Name <a@b>``) or a MIME encoded-word (``=?...?=``).
"""
from alembic import op

revision = "20261119_email_sender_tags"
down_revision = "20261118_run_ledger"
branch_labels = None
depends_on = None

STALE_TAG = "(t IS NOT DISTINCT FROM m.sender OR t LIKE '%<%@%>%' OR t LIKE '%=?%?=%')"


def upgrade():
    op.execute(
        f"""
        UPDATE source_item si SET tags = ARRAY(
            SELECT t FROM unnest(si.tags) WITH ORDINALITY AS u(t, n)
            WHERE NOT {STALE_TAG} ORDER BY n
        )
        FROM email_attachment a JOIN mail_message m ON m.id = a.mail_message_id
        WHERE a.id = si.id AND EXISTS (SELECT 1 FROM unnest(si.tags) t WHERE {STALE_TAG})
        """
    )


def downgrade():
    pass
