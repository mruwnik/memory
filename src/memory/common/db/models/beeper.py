"""Beeper accounts, the chat networks they connect, their chats and participants.

A BeeperAccount is one Beeper (Matrix) account signed in on one Beeper Server,
owned by the memory user who signed it in. Each chat network connected in the
Beeper app (Slack, WhatsApp, ...) is a BeeperNetwork; chats inherit collection,
project and sensitivity from their network unless they override them.
"""

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base
from memory.common.db.models.provider_keys import LLMKeyChoice
from memory.common.encryption import decrypt_value, encrypt_value

if TYPE_CHECKING:
    from memory.common.db.models.sources import Person
    from memory.common.db.models.users import User


class BeeperAccount(Base):
    """A Beeper account signed in on one Beeper Server.

    One Server holds one Beeper account, so ``server_url`` is unique.
    ``beeper_user_id`` is the Matrix user the Server served at sign-in; a
    Server later answering as anyone else is refused.
    """

    __tablename__ = "beeper_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_by_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Origin of the Beeper Server, e.g. http://beeper:23373
    server_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)

    token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Pending sign-in between the email and code steps
    setup_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    beeper_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    homeserver: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The Server's session state: needs-login, initializing, needs-verification, ready, ...
    setup_state: Mapped[str] = mapped_column(
        Text, nullable=False, default="needs-login", server_default="needs-login"
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # X-Beeper-Desktop-Version of the Server's last response
    server_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Whether newly seen networks are collected
    collect_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_reconcile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship("User")
    networks: Mapped[list["BeeperNetwork"]] = relationship(
        "BeeperNetwork", back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("beeper_accounts_owner_idx", "created_by_user_id"),)

    @property
    def token(self) -> str | None:
        if self.token_encrypted is None:
            return None
        return decrypt_value(self.token_encrypted)

    @token.setter
    def token(self, value: str | None) -> None:
        self.token_encrypted = None if value is None else encrypt_value(value)

    @property
    def signed_in(self) -> bool:
        return self.token_encrypted is not None


class BeeperNetwork(LLMKeyChoice, Base):
    """One chat network account in Beeper (an entry of the Server's /v1/accounts)."""

    __tablename__ = "beeper_networks"
    data_source_type: ClassVar[str] = "beeper_network"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("beeper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    # Beeper's accountID, e.g. "slackgo.T0CEP3GKTTK-U0CESAPMP0X"
    network_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    network: Mapped[str] = mapped_column(Text, nullable=False)
    bridge_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    bridge_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # connected, connecting, backfilling, connection_required, ...
    status: Mapped[str | None] = mapped_column(Text, nullable=True)

    collect_messages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[BeeperAccount] = relationship("BeeperAccount", back_populates="networks")
    chats: Mapped[list["BeeperChat"]] = relationship(
        "BeeperChat", back_populates="network", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("account_id", "network_account_id", name="uq_beeper_networks_account"),
        CheckConstraint(
            "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
            name="valid_beeper_network_sensitivity",
        ),
        Index("beeper_networks_project_idx", "project_id"),
    )

    @property
    def content_owner_id(self) -> int | None:
        return self.account.created_by_user_id

    @property
    def llm_registrant_id(self) -> int | None:
        return self.account.created_by_user_id


class BeeperChat(Base):
    """A Beeper chat and its collection settings.

    A merged chat (one holding ``merge``) has no messages of its own; its
    member chats carry ``merged_into`` and are what gets collected.
    """

    __tablename__ = "beeper_chats"
    data_source_type: ClassVar[str] = "beeper_chat"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    network_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("beeper_networks.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    local_chat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "single" or "group"
    chat_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_merged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    merged_into: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_expiry_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # null = inherit from the network
    collect_messages: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Message cursors: reconcile reads after newest_cursor, backfill before oldest_cursor
    newest_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    oldest_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfill_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    network: Mapped[BeeperNetwork] = relationship("BeeperNetwork", back_populates="chats")

    __table_args__ = (
        UniqueConstraint("network_id", "chat_id", name="uq_beeper_chats_network_chat"),
        CheckConstraint(
            "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
            name="valid_beeper_chat_sensitivity",
        ),
        Index("beeper_chats_project_idx", "project_id"),
    )

    @property
    def should_collect(self) -> bool:
        if self.is_merged:
            return False
        if self.collect_messages is not None:
            return self.collect_messages
        return self.network.collect_messages

    @property
    def llm_key_holder(self) -> BeeperNetwork:
        return self.network

    @property
    def content_owner_id(self) -> int | None:
        return self.network.content_owner_id


class BeeperParticipant(Base):
    """Someone in a network's chats; ``participant_id`` is Beeper's network-scoped id."""

    __tablename__ = "beeper_participants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    network_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("beeper_networks.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_self: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    person_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    network: Mapped[BeeperNetwork] = relationship("BeeperNetwork")
    person: Mapped["Person | None"] = relationship("Person")

    __table_args__ = (
        UniqueConstraint("network_id", "participant_id", name="uq_beeper_participants_network"),
        Index("beeper_participants_person_idx", "person_id"),
    )

    @property
    def name(self) -> str:
        return self.full_name or self.username or self.participant_id
