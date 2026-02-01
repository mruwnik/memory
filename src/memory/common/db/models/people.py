"""
Database models for tidbits of information about people.

Note: The Person model itself is defined in sources.py to avoid circular imports.
PersonTidbit extends SourceItem and stores searchable information about people.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Sequence

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from memory.common.db.models.sources import Person
    from memory.common.db.models.users import User

import memory.common.extract as extract
from memory.common.db.models.source_item import (
    SourceItem,
    SourceItemPayload,
)


class PersonPayload(SourceItemPayload):
    """Payload for Person data.

    Note: Person no longer extends SourceItem, but this payload still extends
    SourceItemPayload for backwards compatibility with code that expects
    certain fields (tags, project_id, etc.) to be present. These fields
    are now associated with PersonTidbit instead.
    """

    identifier: Annotated[str, "Unique identifier/slug for the person"]
    display_name: Annotated[str, "Display name of the person"]
    aliases: Annotated[list[str], "Alternative names/handles for the person"]
    contact_info: Annotated[dict, "Contact information (email, phone, etc.)"]
    user_id: Annotated[int | None, "Linked system user ID, if any"]


class PersonTidbitPayload(SourceItemPayload):
    person_id: Annotated[int, "ID of the associated Person"]
    person_identifier: Annotated[str, "Identifier of the associated Person"]
    creator_id: Annotated[int | None, "ID of the user who created this tidbit"]
    tidbit_type: Annotated[str, "Type of tidbit (note, preference, fact, etc.)"]


class PersonTidbit(SourceItem):
    """A piece of information about a person.

    Tidbits extend SourceItem, so they:
    - Have content, tags, project_id, sensitivity (for access control)
    - Get chunked and embedded for search
    - Support creator-based access control (via inherited creator_id)
    """

    __tablename__ = "person_tidbits"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    # Note: creator_id is inherited from SourceItem
    tidbit_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="note")

    __mapper_args__ = {
        "polymorphic_identity": "person_tidbit",
    }

    # Relationships
    person: Mapped["Person"] = relationship("Person", back_populates="tidbits")
    # creator relationship uses inherited creator_id from SourceItem
    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys="SourceItem.creator_id"
    )

    __table_args__ = (
        Index("person_tidbits_person_idx", "person_id"),
        Index("person_tidbits_type_idx", "tidbit_type"),
    )

    def as_payload(self) -> PersonTidbitPayload:
        return PersonTidbitPayload(
            **super().as_payload(),
            person_id=self.person_id,
            person_identifier=self.person.identifier if self.person else "",
            creator_id=self.creator_id,
            tidbit_type=self.tidbit_type,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Create searchable chunks from tidbit data."""
        parts = []

        if self.person:
            parts.append(f"# About {self.person.display_name}")

        parts.append(f"Type: {self.tidbit_type}")

        if self.tags:
            tags_str = ", ".join(self.tags)
            parts.append(f"Tags: {tags_str}")

        if self.content:
            parts.append(f"\n{self.content}")

        text = "\n".join(parts)
        return extract.extract_text(text, modality="person_tidbit")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["person_tidbit"]

    @property
    def title(self) -> str | None:
        """Return a display title for this tidbit."""
        if self.person:
            return f"{self.person.display_name}: {self.tidbit_type}"
        return self.tidbit_type

    @property
    def display_contents(self) -> dict:
        payload = dict(self.as_payload())
        payload.pop("source_id", None)
        return {
            **payload,
            "content": self.content,
            "person_name": self.person.display_name if self.person else None,
        }
