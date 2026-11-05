"""Which Beeper accounts memory may read, and how. Kept light: the collector imports it."""

from dataclasses import dataclass

from memory.common import beeper
from memory.common.db.models import BeeperAccount


@dataclass(frozen=True)
class Access:
    account_id: int
    server_url: str
    token: str

    def client(self) -> beeper.BeeperClient:
        return beeper.BeeperClient(self.server_url, self.token)


def account_access(account: BeeperAccount | None) -> Access | None:
    """How to read an account's Server, if it is active, verified and allowed."""
    if account is None or not (account.is_active and account.verified):
        return None
    token = account.token
    if token is None or not beeper.server_url_allowed(account.server_url):
        return None
    return Access(account.id, account.server_url, token)
