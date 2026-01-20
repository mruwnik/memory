from __future__ import annotations

import textwrap
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.mcp import MCPServerAssignment as MCPServerAssignmentType


class MCPServer(Base):
    """MCP server configuration and OAuth state."""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # MCP server info
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mcp_server_url: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    available_tools: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    disabled_tools: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    # OAuth flow state (temporary, cleared after token exchange)
    state: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)

    # OAuth tokens (set after successful authorization)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    assignments: Mapped[list[MCPServerAssignmentType]] = relationship(
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mcp_server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mcp_servers.id"), nullable=False
    )

    # Polymorphic entity reference
    entity_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # "User", "DiscordUser", "DiscordServer", "DiscordChannel"
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Timestamps
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    mcp_server: Mapped[MCPServer] = relationship("MCPServer", back_populates="assignments")

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
