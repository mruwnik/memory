#! /usr/bin/env python

import argparse
import os
import secrets
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models.users import HumanUser, BotUser, User
from memory.common.modules import MULTI_USER
from memory.common.people import find_or_create_person
from memory.common.scopes import SCOPE_ADMIN


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", type=str, required=True)
    parser.add_argument("--name", type=str, required=True)
    parser.add_argument(
        "--password",
        type=str,
        required=False,
        default=os.environ.get("ADD_USER_PASSWORD"),
        help="Password (defaults to $ADD_USER_PASSWORD, which keeps it out of argv)",
    )
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Do nothing if a user with this email already exists",
    )
    parser.add_argument("--bot", action="store_true", help="Create a bot user")
    parser.add_argument(
        "--admin", action="store_true", help="Grant full access (the * scope)"
    )
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
    single_user = MULTI_USER not in settings.ENABLED_MODULES

    with make_session() as session:
        if args.if_missing and session.query(User).filter_by(email=args.email).first():
            print(f"User {args.email} already exists")
            raise SystemExit(0)
        if args.bot:
            user = BotUser.create_with_api_key(
                name=args.name, email=args.email, api_key=args.api_key
            )
            print(f"Bot user {args.email} created with API key: {user.api_key}")
        else:
            if single_user and session.query(HumanUser).count():
                raise SystemExit(
                    "A human user already exists; set ENABLE_MODULES=multi_user to add more"
                )
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

        # A single-user install has nobody else to grant the rest of the scopes.
        if args.admin or (single_user and not args.bot):
            user.scopes = [SCOPE_ADMIN]
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
