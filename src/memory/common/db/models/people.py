"""
Database models for tracking people.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any, Sequence

import yaml

from sqlalchemy import (
    ARRAY,
    BigInteger,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from memory.common.db.models.discord import DiscordUser
    from memory.common.db.models.users import User

import memory.common.extract as extract
from memory.common import settings

from memory.common.db.models.source_item import (
    SourceItem,
    SourceItemPayload,
)


class PersonPayload(SourceItemPayload):
    identifier: Annotated[str, "Unique identifier/slug for the person"]
    display_name: Annotated[str, "Display name of the person"]
    aliases: Annotated[list[str], "Alternative names/handles for the person"]
    contact_info: Annotated[dict, "Contact information (email, phone, etc.)"]
    user_id: Annotated[int | None, "Linked system user ID, if any"]


class Person(SourceItem):
    """A person you know or want to track."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    identifier: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{}", nullable=False
    )
    contact_info: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    # Optional link to system user account
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user: Mapped["User | None"] = relationship("User", back_populates="person")

    # Relationship to linked Discord accounts
    discord_accounts: Mapped[list[DiscordUser]] = relationship(
        "DiscordUser", back_populates="person"
    )

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
            identifier=self.identifier,
            display_name=self.display_name,
            aliases=self.aliases or [],
            contact_info=self.contact_info or {},
            user_id=self.user_id,
        )

    @property
    def display_contents(self) -> dict:
        result = {
            "identifier": self.identifier,
            "display_name": self.display_name,
            "aliases": self.aliases,
            "contact_info": self.contact_info,
            "notes": self.content,
            "tags": self.tags,
        }
        if self.user_id:
            result["user_id"] = self.user_id
            if self.user:
                result["user_email"] = self.user.email
                result["user_name"] = self.user.name
        return result

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Create searchable chunks from person data."""
        parts = [f"# {self.display_name}"]

        if self.aliases:
            aliases_str = ", ".join(self.aliases)
            parts.append(f"Also known as: {aliases_str}")

        if self.tags:
            tags_str = ", ".join(self.tags)
            parts.append(f"Tags: {tags_str}")

        if self.content:
            parts.append(f"\n{self.content}")

        text = "\n".join(parts)
        return extract.extract_text(text, modality="person")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["person"]

    def to_profile_markdown(self) -> str:
        """Serialize Person to markdown with YAML frontmatter."""
        frontmatter: dict[str, Any] = {
            "identifier": self.identifier,
            "display_name": self.display_name,
        }
        if self.aliases:
            frontmatter["aliases"] = list(self.aliases)
        if self.contact_info:
            frontmatter["contact_info"] = dict(self.contact_info)
        if self.tags:
            frontmatter["tags"] = list(self.tags)

        yaml_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
        parts = ["---", yaml_str.strip(), "---"]

        if self.content:
            parts.append("")
            parts.append(self.content)

        return "\n".join(parts)

    @classmethod
    def from_profile_markdown(cls, content: str) -> dict:
        """Parse profile markdown with YAML frontmatter into Person fields."""
        # Match YAML frontmatter between --- delimiters
        frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n?"
        match = re.match(frontmatter_pattern, content, re.DOTALL)

        if not match:
            # No frontmatter, return empty dict
            return {"notes": content.strip() if content.strip() else None}

        yaml_content = match.group(1)
        body = content[match.end() :].strip()

        try:
            data = yaml.safe_load(yaml_content) or {}
        except yaml.YAMLError:
            return {"notes": content.strip() if content.strip() else None}

        result = {}
        if "identifier" in data:
            result["identifier"] = data["identifier"]
        if "display_name" in data:
            result["display_name"] = data["display_name"]
        if "aliases" in data:
            result["aliases"] = data["aliases"]
        if "contact_info" in data:
            result["contact_info"] = data["contact_info"]
        if "tags" in data:
            result["tags"] = data["tags"]
        if body:
            result["notes"] = body

        return result

    def get_profile_path(self) -> str:
        """Get the relative path for this person's profile note."""
        return f"{settings.PROFILES_FOLDER}/{self.identifier}.md"

    def save_profile_note(self) -> None:
        """Save this person's data to a profile note file."""
        path = settings.NOTES_STORAGE_DIR / self.get_profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_profile_markdown())
