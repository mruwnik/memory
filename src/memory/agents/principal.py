"""Who an unattended run (cron, trigger) acts as, re-checked each time it starts."""

from memory.api.auth import TokenLookup, system_scopes
from memory.common.db.connection import DBSession
from memory.common.db.models import APIKey, User
from memory.common.scopes import SCOPE_ADMIN, SCOPE_AGENTS


class PrincipalRevoked(ValueError):
    """The credentials that set up an unattended run no longer allow it."""


def standing_key(db: DBSession, user: User, key_id: int | None) -> APIKey | None:
    """The API key an unattended run acts through (None: the user's own access).

    Raises PrincipalRevoked if that key is gone or invalid, or if the resulting
    scopes no longer include agents: whoever set the run up has lost the right.
    """
    key = None
    if key_id is not None:
        key = db.get(APIKey, key_id)
        if key is None or not key.is_valid():
            raise PrincipalRevoked(f"the API key that set this up ({key_id}) is no longer valid")
    scopes = system_scopes(TokenLookup(user=user, api_key_record=key))
    if not {SCOPE_AGENTS, SCOPE_ADMIN} & set(scopes):
        raise PrincipalRevoked(f"the '{SCOPE_AGENTS}' scope is no longer granted")
    return key
