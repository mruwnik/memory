from memory.common.audit import record_audit
from memory.common.db.models import AuditEvent


def test_record_audit_commits_its_own_row(db_session, admin_user):
    record_audit("mcp.call", actor_id=admin_user.id, target="upsert", attributes={"name": "Acme"})

    db_session.expire_all()
    row = db_session.query(AuditEvent).one()
    assert (row.actor_id, row.actor_email, row.action, row.target, row.attributes) == (
        admin_user.id,
        "admin@example.com",
        "mcp.call",
        "upsert",
        {"name": "Acme"},
    )


def test_audit_rows_outlive_their_actor(db_session, admin_user):
    record_audit("settings.restart", actor_id=admin_user.id)

    db_session.delete(admin_user)
    db_session.commit()

    db_session.expire_all()
    row = db_session.query(AuditEvent).one()
    assert (row.actor_id, row.actor_email, row.action) == (None, "admin@example.com", "settings.restart")
