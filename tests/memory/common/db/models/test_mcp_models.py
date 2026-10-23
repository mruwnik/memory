import json

import pytest
from sqlalchemy.exc import IntegrityError

from memory.common.db.models import ExternalMCPServer

SECRETS = {
    "access_token": "access-SECRET-1",
    "refresh_token": "refresh-SECRET-2",
    "client_secret": "client-SECRET-3",
}


def make_server(db, user, name="linear", **kwargs):
    server = ExternalMCPServer(
        user_id=user.id,
        name=name,
        url="https://mcp.example.com/mcp",
        transport="http",
        status="pending_auth",
        **({"auth_type": "oauth"} | kwargs),
    )
    db.add(server)
    db.commit()
    return server


@pytest.mark.parametrize("field", sorted(SECRETS))
def test_secrets_are_encrypted_at_rest(db_session, regular_user, field):
    server = make_server(db_session, regular_user)
    setattr(server, field, SECRETS[field])
    db_session.commit()
    db_session.expire_all()

    raw = getattr(server, f"{field}_encrypted")
    assert SECRETS[field].encode() not in raw
    assert getattr(server, field) == SECRETS[field]


def test_headers_are_encrypted_at_rest(db_session, regular_user):
    server = make_server(db_session, regular_user, auth_type="headers")
    server.headers = {"Authorization": "Bearer hdr-SECRET-4"}
    db_session.commit()
    db_session.expire_all()

    assert b"hdr-SECRET-4" not in (server.headers_encrypted or b"")
    assert server.headers == {"Authorization": "Bearer hdr-SECRET-4"}


@pytest.mark.parametrize("field", ["access_token", "refresh_token", "client_secret"])
def test_clearing_a_secret_clears_its_column(db_session, regular_user, field):
    server = make_server(db_session, regular_user)
    setattr(server, field, "x")
    setattr(server, field, None)

    assert getattr(server, f"{field}_encrypted") is None


def test_serialize_shows_no_secret_values(db_session, regular_user):
    server = make_server(db_session, regular_user, client_id="cid")
    for field, value in SECRETS.items():
        setattr(server, field, value)
    server.headers = {"X-Api-Key": "hdr-SECRET-4"}
    db_session.commit()

    out = server.serialize()
    dumped = json.dumps(out, default=str)

    assert not [s for s in [*SECRETS.values(), "hdr-SECRET-4"] if s in dumped]
    assert out["header_names"] == ["X-Api-Key"]
    assert out["has_token"] is True
    assert out["client_id"] == "cid"


def test_names_are_unique_per_user(db_session, regular_user, admin_user):
    make_server(db_session, regular_user)
    make_server(db_session, admin_user)

    with pytest.raises(IntegrityError):
        make_server(db_session, regular_user)
