#!/usr/bin/env python
"""
CLI tool for managing access control.

Projects use team-based access control. Teams are assigned to projects,
and users access projects through their team memberships.

Usage:
    # List projects
    python tools/access_control.py list-projects

    # List teams assigned to a project
    python tools/access_control.py list-teams --project-id 123

    # Grant superadmin (add admin scope)
    python tools/access_control.py grant-admin --email user@example.com

    # Revoke superadmin (remove admin scope)
    python tools/access_control.py revoke-admin --email user@example.com

    # Classify existing content to a project
    python tools/access_control.py classify-content --project-id 123 --sensitivity internal --modality mail

    # List unclassified content
    python tools/access_control.py list-unclassified
"""

import argparse
import sys

from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem
from memory.common.db.models.sources import Project
from memory.common.db.models.users import User


def list_projects(args):  # noqa: ARG001
    """List all projects."""
    with make_session() as session:
        projects = session.query(Project).order_by(Project.id).all()
        if not projects:
            print("No projects found")
            return

        print(f"{'ID':<8} {'Slug':<40} {'Title':<30} {'Teams':<12}")
        print("-" * 95)
        for project in projects:
            slug = project.slug
            team_count = len(project.teams) if project.teams else 0
            title = (project.title or "")[:30]
            print(f"{project.id:<8} {slug:<40} {title:<30} {team_count:<12}")


def list_teams(args):
    """List teams assigned to a project."""
    with make_session() as session:
        project = session.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Error: Project with ID {args.project_id} not found")
            sys.exit(1)

        teams = project.teams
        if not teams:
            print(f"No teams assigned to project '{project.slug}'")
            return

        print(f"Teams assigned to project '{project.slug}':")
        print(f"{'Team ID':<12} {'Name':<30} {'Slug':<20} {'Members':<10}")
        print("-" * 75)
        for team in teams:
            member_count = len(team.members) if team.members else 0
            print(f"{team.id:<12} {team.name:<30} {team.slug:<20} {member_count:<10}")


def grant_admin(args):
    """Grant superadmin access to a user by adding 'admin' scope."""
    with make_session() as session:
        user = session.query(User).filter(User.email == args.email).first()
        if not user:
            print(f"Error: User '{args.email}' not found")
            sys.exit(1)

        scopes = list(user.scopes or [])
        if "admin" not in scopes:
            scopes.append("admin")
            user.scopes = scopes
            session.commit()
            print(f"Granted admin scope to {args.email}")
        else:
            print(f"User {args.email} already has admin scope")


def revoke_admin(args):
    """Revoke superadmin access from a user by removing 'admin' scope."""
    with make_session() as session:
        user = session.query(User).filter(User.email == args.email).first()
        if not user:
            print(f"Error: User '{args.email}' not found")
            sys.exit(1)

        scopes = list(user.scopes or [])
        if "admin" in scopes:
            scopes.remove("admin")
            user.scopes = scopes
            session.commit()
            print(f"Revoked admin scope from {args.email}")
        elif "*" in scopes:
            print("Warning: User has '*' scope which also grants admin. Consider removing that too.")
        else:
            print(f"User {args.email} does not have admin scope")


def classify_content(args):
    """Classify existing content to a project."""
    with make_session() as session:
        project = session.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Error: Project with ID {args.project_id} not found")
            sys.exit(1)

        query = session.query(SourceItem).filter(SourceItem.project_id.is_(None))

        if args.modality:
            query = query.filter(SourceItem.modality == args.modality)

        if args.limit:
            query = query.limit(args.limit)

        items = query.all()
        if not items:
            print("No unclassified items found matching criteria")
            return

        count = 0
        for item in items:
            item.project_id = project.id
            item.sensitivity = args.sensitivity
            count += 1

        session.commit()
        print(f"Classified {count} items to project '{project.slug}' with sensitivity '{args.sensitivity}'")


def list_unclassified(args):  # noqa: ARG001
    """List count of unclassified content by modality."""
    with make_session() as session:
        from sqlalchemy import func

        results = (
            session.query(SourceItem.modality, func.count(SourceItem.id))
            .filter(SourceItem.project_id.is_(None))
            .group_by(SourceItem.modality)
            .all()
        )

        if not results:
            print("No unclassified items found")
            return

        print("Unclassified items by modality:")
        print(f"{'Modality':<20} {'Count':<10}")
        print("-" * 30)
        total = 0
        for modality, count in sorted(results, key=lambda x: -x[1]):
            print(f"{modality:<20} {count:<10}")
            total += count
        print("-" * 30)
        print(f"{'Total':<20} {total:<10}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Access control management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-projects
    list_projects_parser = subparsers.add_parser("list-projects", help="List all projects")
    list_projects_parser.set_defaults(func=list_projects)

    # list-teams
    list_teams_parser = subparsers.add_parser("list-teams", help="List teams assigned to a project")
    list_teams_parser.add_argument("--project-id", type=int, required=True, help="Project ID")
    list_teams_parser.set_defaults(func=list_teams)

    # grant-admin
    grant_admin_parser = subparsers.add_parser("grant-admin", help="Grant admin scope to user")
    grant_admin_parser.add_argument("--email", required=True, help="User email")
    grant_admin_parser.set_defaults(func=grant_admin)

    # revoke-admin
    revoke_admin_parser = subparsers.add_parser("revoke-admin", help="Revoke admin scope from user")
    revoke_admin_parser.add_argument("--email", required=True, help="User email")
    revoke_admin_parser.set_defaults(func=revoke_admin)

    # classify-content
    classify_parser = subparsers.add_parser("classify-content", help="Classify content to a project")
    classify_parser.add_argument("--project-id", type=int, required=True, help="Project ID")
    classify_parser.add_argument(
        "--sensitivity",
        required=True,
        choices=["basic", "internal", "confidential"],
        help="Sensitivity level",
    )
    classify_parser.add_argument("--modality", help="Filter by modality (mail, blog, etc.)")
    classify_parser.add_argument("--limit", type=int, help="Limit number of items to classify")
    classify_parser.set_defaults(func=classify_content)

    # list-unclassified
    unclassified_parser = subparsers.add_parser("list-unclassified", help="List unclassified content")
    unclassified_parser.set_defaults(func=list_unclassified)

    args = parser.parse_args()
    args.func(args)
