#! /usr/bin/env python

import argparse
import os
from memory.common.db.connection import make_session
from memory.common.db.models.users import APIKey, HumanUser, BotUser, User
from memory.common.module_settings import enabled_modules
from memory.common.modules import MULTI_USER
from memory.common.people import find_person_by_email
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
        help="Don't create an API key",
    )
    args = parser.parse_args()
    # Only a hash is stored, which is safe only for a key too long to guess.
    if args.api_key is not None and len(args.api_key) < 32:
        raise SystemExit("--api-key must be at least 32 characters")
    single_user = MULTI_USER not in enabled_modules()

    with make_session() as session:
        if args.if_missing and session.query(User).filter_by(email=args.email).first():
            print(f"User {args.email} already exists")
            raise SystemExit(0)
        if args.bot:
            user = BotUser(name=args.name, email=args.email)
        else:
            if single_user and session.query(HumanUser).count():
                raise SystemExit(
                    "A human user already exists; turn on the multi_user module (Server settings, or ENABLE_MODULES=multi_user) to add more"
                )
            if not args.password:
                raise ValueError("Password required for human users")
            user = HumanUser.create_with_password(
                email=args.email, password=args.password, name=args.name
            )

        # A single-user install has nobody else to grant the rest of the scopes.
        if args.admin or (single_user and not args.bot):
            user.scopes = [SCOPE_ADMIN]
        session.add(user)
        session.commit()
        session.refresh(user)
        if args.no_api_key:
            print(f"User {args.email} created (no API key)")
        else:
            api_key = APIKey.create(user_id=user.id)
            if args.api_key:
                api_key.key = args.api_key
            session.add(api_key)
            session.commit()
            print(f"User {args.email} created with API key: {api_key.key}")

        # Email only: names and aliases are free text whoever created the Person chose.
        person = find_person_by_email(session, args.email)
        if person and person.user_id is None:
            person.user_id = user.id
            session.commit()
            print(f"Auto-linked to person: {person.identifier} (id={person.id})")
        elif person and person.user_id is not None:
            print(f"Person {person.identifier} already linked to user {person.user_id}")
        else:
            print("No matching person record found for auto-linking")
