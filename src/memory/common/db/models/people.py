"""
Database models for tracking people.
"""

from typing import Annotated, Sequence, cast

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

import memory.common.extract as extract

from memory.common.db.models.source_item import (
    SourceItem,
    SourceItemPayload,
)


class PersonPayload(SourceItemPayload):
    identifier: Annotated[str, "Unique identifier/slug for the person"]
    display_name: Annotated[str, "Display name of the person"]
    aliases: Annotated[list[str], "Alternative names/handles for the person"]
    contact_info: Annotated[dict, "Contact information (email, phone, etc.)"]


class Person(SourceItem):
    """A person you know or want to track."""

    __tablename__ = "people"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    identifier = Column(Text, unique=True, nullable=False, index=True)
    display_name = Column(Text, nullable=False)
    aliases = Column(ARRAY(Text), server_default="{}", nullable=False)
    contact_info = Column(JSONB, server_default="{}", nullable=False)

    __mapper_args__ = {
        "polymorphic_identity": "person",
    }

    __table_args__ = (
        Index("person_identifier_idx", "identifier"),
        Index("person_display_name_idx", "display_name"),
        Index("person_aliases_idx", "aliases", postgresql_using="gin"),
    )

    def as_payload(self) -> PersonPayload:
        return PersonPayload(
            **super().as_payload(),
            identifier=cast(str, self.identifier),
            display_name=cast(str, self.display_name),
            aliases=cast(list[str], self.aliases) or [],
            contact_info=cast(dict, self.contact_info) or {},
        )

    @property
    def display_contents(self) -> dict:
        return {
            "identifier": self.identifier,
            "display_name": self.display_name,
            "aliases": self.aliases,
            "contact_info": self.contact_info,
            "notes": self.content,
            "tags": self.tags,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Create searchable chunks from person data."""
        parts = [f"# {self.display_name}"]

        if self.aliases:
            aliases_str = ", ".join(cast(list[str], self.aliases))
            parts.append(f"Also known as: {aliases_str}")

        if self.tags:
            tags_str = ", ".join(cast(list[str], self.tags))
            parts.append(f"Tags: {tags_str}")

        if self.content:
            parts.append(f"\n{self.content}")

        text = "\n".join(parts)
        return extract.extract_text(text, modality="person")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["person"]
