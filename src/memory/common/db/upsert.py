"""Race-safe row creation for rows several workers may create at once."""

from typing import Any

from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert

from memory.common.db.connection import DBSession


def insert_missing(session: DBSession, model: type, **values: Any) -> bool:
    """Insert a row unless one with its key exists; True if this call made it.
    A plain get-then-insert would raise on the loser of a race."""
    stmt = pg_insert(model).values(**values).on_conflict_do_nothing().returning(literal_column("1"))
    return session.execute(stmt).first() is not None


def get_or_insert[T](session: DBSession, model: type[T], key: Any, **values: Any) -> tuple[T, bool]:
    """The row with this key, inserted from ``values`` if missing; True if this call made it."""
    if (row := session.get(model, key)) is not None:
        return row, False
    created = insert_missing(session, model, **values)
    return session.get_one(model, key), created
