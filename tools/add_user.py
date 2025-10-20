#! /usr/bin/env python

import argparse
from memory.common.db.connection import make_session
from memory.common.db.models.users import HumanUser, BotUser


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--email", type=str, required=True)
    args.add_argument("--name", type=str, required=True)
    args.add_argument("--password", type=str, required=False)
    args.add_argument("--bot", action="store_true", help="Create a bot user")
    args.add_argument(
        "--api-key",
        type=str,
        required=False,
        help="API key for bot user (auto-generated if not provided)",
    )
    args = args.parse_args()

    with make_session() as session:
        if args.bot:
            user = BotUser.create_with_api_key(
                name=args.name, email=args.email, api_key=args.api_key
            )
            print(f"Bot user {args.email} created with API key: {user.api_key}")
        else:
            if not args.password:
                raise ValueError("Password required for human users")
            user = HumanUser.create_with_password(
                email=args.email, password=args.password, name=args.name
            )
            print(f"Human user {args.email} created")

        session.add(user)
        session.commit()

    if args.bot:
        print(f"Bot user {args.email} created with API key: {user.api_key}")
    else:
        print(f"Human user {args.email} created")
