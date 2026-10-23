import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from memory.common.db.models.base import Base
from memory.common.db.models.secrets import decrypt_value, encrypt_value


class ServerStatus(StrEnum):
    PENDING_AUTH = "pending_auth"
    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"
    ERROR = "error"


TRANSPORTS = ("http", "sse")
AUTH_TYPES = ("oauth", "headers", "none")


def encrypted(column: str) -> property:
    def load(self) -> str | None:
        raw = getattr(self, column)
        return None if raw is None else decrypt_value(raw)

    def store(self, value: str | None) -> None:
        setattr(self, column, None if value is None else encrypt_value(value))

    return property(load, store)


class ExternalMCPServer(Base):
    """A remote MCP server one user registered, with that user's credentials."""

    __tablename__ = "external_mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_detail: Mapped[str | None] = mapped_column(Text)

    headers_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)

    # Stored at authorize time so a refresh never re-discovers.
    issuer: Mapped[str | None] = mapped_column(Text)
    # RFC 9207: the issuer promised `iss` on authorization responses.
    iss_parameter_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    authorization_endpoint: Mapped[str | None] = mapped_column(Text)
    token_endpoint: Mapped[str | None] = mapped_column(Text)
    revocation_endpoint: Mapped[str | None] = mapped_column(Text)
    resource: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[str | None] = mapped_column(Text)
    client_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_endpoint_auth_method: Mapped[str | None] = mapped_column(Text)
    client_source: Mapped[str | None] = mapped_column(Text)
    access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tools: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]", default=list
    )
    tools_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tools_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="external_mcp_servers_user_name"),
        CheckConstraint(
            "transport IN ('http', 'sse')", name="external_mcp_servers_transport"
        ),
        CheckConstraint(
            "auth_type IN ('oauth', 'headers', 'none')",
            name="external_mcp_servers_auth_type",
        ),
        CheckConstraint(
            "status IN ('pending_auth', 'connected', 'needs_reauth', 'error')",
            name="external_mcp_servers_status",
        ),
    )

    access_token = encrypted("access_token_encrypted")
    refresh_token = encrypted("refresh_token_encrypted")
    client_secret = encrypted("client_secret_encrypted")

    @property
    def headers(self) -> dict[str, str]:
        if self.headers_encrypted is None:
            return {}
        return json.loads(decrypt_value(self.headers_encrypted))

    @headers.setter
    def headers(self, value: dict[str, str] | None) -> None:
        self.headers_encrypted = encrypt_value(json.dumps(value)) if value else None

    def clear_tokens(self) -> None:
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None

    def clear_client(self) -> None:
        self.iss_parameter_supported = False
        for field in (
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "revocation_endpoint",
            "resource",
            "client_id",
            "token_endpoint_auth_method",
            "client_source",
        ):
            setattr(self, field, None)
        self.client_secret = None

    def serialize(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "transport": self.transport,
            "auth_type": self.auth_type,
            "status": self.status,
            "status_detail": self.status_detail,
            "header_names": sorted(self.headers),
            "issuer": self.issuer,
            "scopes": self.scopes,
            "client_id": self.client_id,
            "client_source": self.client_source,
            "has_token": self.access_token_encrypted is not None,
            "token_expires_at": iso(self.token_expires_at),
            "tools": self.tools,
            "tools_refreshed_at": iso(self.tools_refreshed_at),
            "tools_changed_at": iso(self.tools_changed_at),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }
