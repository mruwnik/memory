import textwrap

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class MCPServer(Base):
    """MCP server configuration and OAuth state."""

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True)

    # MCP server info
    name = Column(Text, nullable=False)
    mcp_server_url = Column(Text, nullable=False)
    client_id = Column(Text, nullable=False)
    available_tools = Column(ARRAY(Text), nullable=False, server_default="{}")

    # OAuth flow state (temporary, cleared after token exchange)
    state = Column(Text, nullable=True, unique=True)
    code_verifier = Column(Text, nullable=True)

    # OAuth tokens (set after successful authorization)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    assignments = relationship(
        "MCPServerAssignment", back_populates="mcp_server", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("mcp_state_idx", "state"),)

    def as_xml(self) -> str:
        tools = "\n".join(f"• {tool}" for tool in self.available_tools).strip()
        return textwrap.dedent("""
            <mcp_server>
                <name>
                    {name}
                </name>
                <mcp_server_url>
                    {mcp_server_url}
                </mcp_server_url>
                <client_id>
                    {client_id}
                </client_id>
                <available_tools>
                    {available_tools}
                </available_tools>
            </mcp_server>
        """).format(
            name=self.name,
            mcp_server_url=self.mcp_server_url,
            client_id=self.client_id,
            available_tools=tools,
        )


class MCPServerAssignment(Base):
    """Assignment of MCP servers to entities (users, channels, servers, etc.)."""

    __tablename__ = "mcp_server_assignments"

    id = Column(Integer, primary_key=True)
    mcp_server_id = Column(Integer, ForeignKey("mcp_servers.id"), nullable=False)

    # Polymorphic entity reference
    entity_type = Column(
        Text, nullable=False
    )  # "User", "DiscordUser", "DiscordServer", "DiscordChannel"
    entity_id = Column(BigInteger, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    mcp_server = relationship("MCPServer", back_populates="assignments")

    __table_args__ = (
        Index("mcp_assignment_entity_idx", "entity_type", "entity_id"),
        Index("mcp_assignment_server_idx", "mcp_server_id"),
        Index(
            "mcp_assignment_unique_idx",
            "mcp_server_id",
            "entity_type",
            "entity_id",
            unique=True,
        ),
    )
