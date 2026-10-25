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

from cryptography.fernet import InvalidToken

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import rekey_all_secrets


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
