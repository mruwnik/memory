#! /usr/bin/env python

import argparse
import secrets
from memory.common.db.connection import make_session
from memory.common.db.models.users import HumanUser, BotUser
from memory.common.people import find_or_create_person


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", type=str, required=True)
    parser.add_argument("--name", type=str, required=True)
    parser.add_argument("--password", type=str, required=False)
    parser.add_argument("--bot", action="store_true", help="Create a bot user")
    parser.add_argument(
        "--api-key",
        type=str,
        required=False,
        help="API key (auto-generated if not provided)",
    )
    parser.add_argument(
        "--no-api-key",
        action="store_true",
        help="Don't generate an API key for human users",
    )
    args = parser.parse_args()

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
            # Set API key for human users too (unless --no-api-key)
            if not args.no_api_key:
                user.api_key = args.api_key or f"user_{secrets.token_hex(32)}"
                print(f"Human user {args.email} created with API key: {user.api_key}")
            else:
                print(f"Human user {args.email} created (no API key)")

        session.add(user)
        session.commit()
        session.refresh(user)

        # create_if_missing=False: CLI user creation only links to existing
        # Person records (no auto-creation). The API path in users.py uses
        # create_if_missing=True so that every registered user gets a Person.
        person, _ = find_or_create_person(
            session,
            name=args.name,
            email=args.email,
            create_if_missing=False,
        )
        if person and person.user_id is None:
            person.user_id = user.id
            session.commit()
            print(f"Auto-linked to person: {person.identifier} (id={person.id})")
        elif person and person.user_id is not None:
            print(f"Person {person.identifier} already linked to user {person.user_id}")
        else:
            print("No matching person record found for auto-linking")
