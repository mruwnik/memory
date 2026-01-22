"""Database models for prediction market watchlist tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class WatchedMarket(Base):
    """
    Tracks prediction markets that a user wants to monitor.

    Stores the market state when added and can track price changes over time.
    """

    __tablename__ = "watched_markets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Market identification
    market_id: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(50))  # manifold, polymarket, kalshi

    # Market info (cached)
    question: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(500))

    # Timestamps
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Price tracking
    price_when_added: Mapped[float | None] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    alert_threshold: Mapped[float | None] = mapped_column(Float)

    # User association
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user: Mapped[User] = relationship("User", backref="watched_markets")

    __table_args__ = (
        UniqueConstraint("user_id", "market_id", "source", name="uq_watched_market"),
        Index("ix_watched_markets_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<WatchedMarket(id={self.id}, market_id={self.market_id}, "
            f"source={self.source}, user_id={self.user_id})>"
        )

    def update_price(self, price: float) -> None:
        """Update the last known price."""
        self.last_price = price
        self.last_updated = datetime.now(timezone.utc)


class WatchedMarketPayload(BaseModel):
    """Pydantic model for API responses."""

    id: int
    market_id: str
    source: str
    question: str | None
    url: str | None
    added_at: datetime
    last_updated: datetime | None
    price_when_added: float | None
    last_price: float | None
    alert_threshold: float | None
    price_change: float | None = None  # Computed field

    model_config = {"from_attributes": True}
