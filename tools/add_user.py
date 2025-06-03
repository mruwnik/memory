#! /usr/bin/env python

import argparse
from memory.common.db.connection import make_session
from memory.common.db.models.users import User


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--email", type=str, required=True)
    args.add_argument("--password", type=str, required=True)
    args.add_argument("--name", type=str, required=True)
    args = args.parse_args()

    with make_session() as session:
        user = User.create_with_password(
            email=args.email, password=args.password, name=args.name
        )
        session.add(user)
        session.commit()

    print(f"User {args.email} created")
