"""Re-encrypt the stored secrets under ROTATE_SECRETS_TO (default: the
configured SECRETS_ENCRYPTION_KEY), from whichever of the whitespace-separated
keys in ROTATE_SECRETS_FROM they are under, in one transaction.

    python -m memory.common.db.rekey

tools/ops/rotate-keys.sh runs it to rotate the key, and the restore scripts
after loading a dump that may predate a rotation. Keys come in through the
environment so they stay out of argv; stdout is a single count.
"""

import os
import sys
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Column, LargeBinary, MetaData, Table, select, update
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.crypto import derive_encryption_key
from memory.common.db.connection import make_session
from memory.common.db.models import Base


def is_encrypted_column(column: Column) -> bool:
    """Whether a column holds Fernet ciphertext produced by encrypt_value."""
    return isinstance(column.type, LargeBinary) and column.name.endswith("_encrypted")


def encrypted_columns(metadata: MetaData = Base.metadata) -> dict[Table, list[Column]]:
    """Every encrypted column in `metadata`: the models', or a database's as reflected."""
    found = {
        table: [c for c in table.columns if is_encrypted_column(c)]
        for table in metadata.tables.values()
    }
    return {table: columns for table, columns in found.items() if columns}


def reencrypted(token: bytes, target: Fernet, sources: MultiFernet) -> bytes | None:
    """`token` encrypted under `target`, or None when it already is."""
    try:
        target.decrypt(token)
    except InvalidToken:
        return target.encrypt(sources.decrypt(token))
    return None


def rekey_all_secrets(session: Session, target: str, candidates: Iterable[str]) -> int:
    """Re-encrypt every stored encrypted value under `target`, each from
    whichever of `candidates` decrypts it.

    Used to rotate SECRETS_ENCRYPTION_KEY, and after loading a dump taken
    before a rotation. The columns come from the database, not the models:
    after a restore or a rollback its schema can be older or newer than this code.

    Returns:
        Number of values re-encrypted

    Raises:
        InvalidToken: If neither `target` nor any candidate decrypts some
            value; the caller must roll back
    """
    salt = settings.SECRETS_ENCRYPTION_SALT
    to = Fernet(derive_encryption_key(target, salt))
    sources = MultiFernet([to, *(Fernet(derive_encryption_key(k, salt)) for k in candidates)])
    live = MetaData()
    live.reflect(bind=session.connection())

    count = 0
    for table, columns in encrypted_columns(live).items():
        pk = list(table.primary_key.columns)
        for row in session.execute(select(*pk, *columns)).mappings().all():
            values = {
                column: value
                for column in columns
                if row[column] is not None
                and (value := reencrypted(bytes(row[column]), to, sources)) is not None
            }
            if not values:
                continue
            session.execute(
                update(table)
                .where(*(column == row[column] for column in pk))
                .values(values)
            )
            count += len(values)

    return count


def main() -> int:
    target = os.getenv("ROTATE_SECRETS_TO") or settings.SECRETS_ENCRYPTION_KEY
    if not target:
        print("no SECRETS_ENCRYPTION_KEY to encrypt under", file=sys.stderr)
        return 2
    candidates = os.getenv("ROTATE_SECRETS_FROM", "").split()
    try:
        with make_session() as session:
            count = rekey_all_secrets(session, target, candidates)
    except InvalidToken:
        print(
            "no known SECRETS_ENCRYPTION_KEY decrypts the stored secrets",
            file=sys.stderr,
        )
        return 1
    print(f"secrets_reencrypted={count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
