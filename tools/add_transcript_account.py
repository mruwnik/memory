#! /usr/bin/env python
"""Create a TranscriptAccount row for a user.

CLI for provisioning per-user transcript-provider credentials (Fireflies,
etc.). The provider list is sourced from
``memory.workers.tasks.transcripts.PROVIDERS`` so the CLI can never accept
a value the worker doesn't know about — see the regression test
``test_cli_provider_choices_match_worker_providers``.
"""

import argparse
import sys

from memory.common.db.connection import make_session
from memory.common.db.models.sources import TranscriptAccount
from memory.common.db.models.users import HumanUser
from memory.workers.tasks.transcripts import PROVIDERS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the argparse parser and parse argv (or sys.argv if None).

    Exposed as a helper so the parser configuration can be exercised from
    tests without invoking the full DB-touching ``main`` path.
    """
    parser = argparse.ArgumentParser(
        description="Create a TranscriptAccount for a user."
    )
    parser.add_argument("--user-email", type=str, required=True,
                        help="Email of the existing HumanUser to attach to")
    parser.add_argument("--name", type=str, required=True,
                        help="Friendly label for this account")
    parser.add_argument("--provider", type=str, required=True,
                        choices=sorted(PROVIDERS.keys()),
                        help="Transcript provider (must match a key in "
                             "memory.workers.tasks.transcripts.PROVIDERS)")
    parser.add_argument("--api-key", type=str, required=True,
                        help="Provider API key (stored encrypted)")
    parser.add_argument("--webhook-secret", type=str, default=None,
                        help="Optional webhook secret (stored encrypted)")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    with make_session() as session:
        user = (
            session.query(HumanUser)
            .filter(HumanUser.email == args.user_email)
            .one_or_none()
        )
        if user is None:
            print(f"No HumanUser found with email {args.user_email!r}",
                  file=sys.stderr)
            return 1
        account = TranscriptAccount(
            user_id=user.id,
            name=args.name,
            provider=args.provider,
        )
        account.api_key = args.api_key
        if args.webhook_secret is not None:
            account.webhook_secret = args.webhook_secret
        session.add(account)
        session.commit()
        print(
            f"Created TranscriptAccount id={account.id} "
            f"provider={account.provider} for user_id={user.id}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
