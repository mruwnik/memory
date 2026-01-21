import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from memory.common.db.models.mcp import MCPServer, MCPServerAssignment


@pytest.mark.parametrize(
    "available_tools,expected_tools",
    [
        (["search", "summarize"], ["• search", "• summarize"]),
        ([], []),
    ],
)
def test_mcp_server_as_xml_formats_available_tools(available_tools, expected_tools):
    server = MCPServer(
        name="Example Server",
        mcp_server_url="https://example.com/mcp",
        client_id="client-123",
        available_tools=available_tools,
    )

    xml_output = server.as_xml()
    root = ET.fromstring(xml_output)

    name_el = root.find("name")
    url_el = root.find("mcp_server_url")
    client_id_el = root.find("client_id")
    assert name_el is not None and name_el.text is not None
    assert url_el is not None and url_el.text is not None
    assert client_id_el is not None and client_id_el.text is not None
    assert name_el.text.strip() == "Example Server"
    assert url_el.text.strip() == "https://example.com/mcp"
    assert client_id_el.text.strip() == "client-123"

    tools_element = root.find("available_tools")
    assert tools_element is not None

    tools_text = tools_element.text.strip() if tools_element.text else ""
    if expected_tools:
        assert tools_text.splitlines() == expected_tools
    else:
        assert tools_text == ""


def test_mcp_server_crud_and_token_expiration(db_session):
    initial_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
    server = MCPServer(
        name="Initial Server",
        mcp_server_url="https://initial.example.com/mcp",
        client_id="client-initial",
        available_tools=["search"],
        access_token="access-123",
        refresh_token="refresh-123",
        token_expires_at=initial_expiry,
    )

    db_session.add(server)
    db_session.commit()

    fetched = db_session.get(MCPServer, server.id)
    assert fetched is not None
    assert fetched.access_token == "access-123"
    assert fetched.refresh_token == "refresh-123"
    assert fetched.token_expires_at == initial_expiry
    assert fetched.token_expires_at.tzinfo is not None

    new_expiry = initial_expiry + timedelta(minutes=15)
    fetched.name = "Updated Server"
    fetched.available_tools = [*fetched.available_tools, "summarize"]
    fetched.access_token = "access-456"
    fetched.refresh_token = "refresh-456"
    fetched.token_expires_at = new_expiry
    db_session.commit()

    updated = db_session.get(MCPServer, server.id)
    assert updated is not None
    assert updated.name == "Updated Server"
    assert updated.available_tools == ["search", "summarize"]
    assert updated.access_token == "access-456"
    assert updated.refresh_token == "refresh-456"
    assert updated.token_expires_at == new_expiry

    db_session.delete(updated)
    db_session.commit()

    assert db_session.get(MCPServer, server.id) is None


def test_mcp_server_assignments_relationship_and_cascade(db_session):
    server = MCPServer(
        name="Cascade Server",
        mcp_server_url="https://cascade.example.com/mcp",
        client_id="client-cascade",
        available_tools=["search"],
    )
    server.assignments.extend(
        [
            MCPServerAssignment(entity_type="DiscordUser", entity_id=101),
            MCPServerAssignment(entity_type="DiscordChannel", entity_id=202),
        ]
    )

    db_session.add(server)
    db_session.commit()

    persisted_server = db_session.get(MCPServer, server.id)
    assert persisted_server is not None
    assert len(persisted_server.assignments) == 2
    assert {assignment.entity_type for assignment in persisted_server.assignments} == {
        "DiscordUser",
        "DiscordChannel",
    }
    assert all(
        assignment.mcp_server_id == persisted_server.id
        for assignment in persisted_server.assignments
    )

    db_session.delete(persisted_server)
    db_session.commit()

    remaining_assignments = db_session.query(MCPServerAssignment).all()
    assert remaining_assignments == []


def test_mcp_server_assignment_unique_constraint(db_session):
    server = MCPServer(
        name="Unique Server",
        mcp_server_url="https://unique.example.com/mcp",
        client_id="client-unique",
        available_tools=["search"],
    )
    assignment = MCPServerAssignment(
        entity_type="DiscordUser",
        entity_id=12345,
    )
    server.assignments.append(assignment)

    db_session.add(server)
    db_session.commit()

    duplicate_assignment = MCPServerAssignment(
        mcp_server_id=server.id,
        entity_type="DiscordUser",
        entity_id=12345,
    )
    db_session.add(duplicate_assignment)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()

    assignments = (
        db_session.query(MCPServerAssignment)
        .filter(MCPServerAssignment.mcp_server_id == server.id)
        .all()
    )
    assert len(assignments) == 1
