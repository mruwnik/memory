"""Who unattended work (agent cron, triggers, scheduled Claude sessions) acts
as, re-checked each time it starts. Worker-safe: no API imports."""

from memory.common.access_control import credential_scopes
from memory.common.db.connection import DBSession
from memory.common.db.models import APIKey, User
from memory.common.scopes import SCOPE_ADMIN


class PrincipalRevoked(ValueError):
    """The credentials that set up an unattended run no longer allow it."""


def standing_key(db: DBSession, user: User, key_id: int | None, scope: str) -> APIKey | None:
    """The API key unattended work acts through (None: the user's own access).

    Raises PrincipalRevoked if that key is gone or invalid, or if the resulting
    scopes no longer include ``scope``: whoever set the work up has lost the right.
    """
    key = None
    if key_id is not None:
        key = db.get(APIKey, key_id)
        if key is None or not key.is_valid():
            raise PrincipalRevoked(f"the API key that set this up ({key_id}) is no longer valid")
    if not {scope, SCOPE_ADMIN} & set(credential_scopes(user, key)):
        raise PrincipalRevoked(f"the '{scope}' scope is no longer granted")
    return key
