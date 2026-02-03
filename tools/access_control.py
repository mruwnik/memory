#!/usr/bin/env python
"""
CLI tool for managing access control projects and collaborators.

Projects are GitHub milestones. Collaborators are Person entries linked via
the project_collaborators junction table.

Usage:
    # List projects (milestones)
    python tools/access_control.py list-projects

    # List collaborators of a project
    python tools/access_control.py list-collaborators --project-id 123

    # Add collaborator to project
    python tools/access_control.py add-collaborator --project-id 123 --person-id 456 --role admin

    # Remove collaborator from project
    python tools/access_control.py remove-collaborator --project-id 123 --person-id 456

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
from memory.common.db.models import Person, SourceItem
from memory.common.db.models.sources import Project, project_collaborators
from memory.common.db.models.users import User


def list_projects(args):
    """List all projects (GitHub milestones)."""
    with make_session() as session:
        projects = session.query(Project).order_by(Project.id).all()
        if not projects:
            print("No projects found")
            return

        print(f"{'ID':<8} {'Slug':<40} {'Title':<30} {'Collaborators':<12}")
        print("-" * 95)
        for project in projects:
            slug = project.slug
            collab_count = len(project.collaborators) if project.collaborators else 0
            title = (project.title or "")[:30]
            print(f"{project.id:<8} {slug:<40} {title:<30} {collab_count:<12}")


def list_collaborators(args):
    """List collaborators of a project."""
    with make_session() as session:
        project = session.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Error: Project with ID {args.project_id} not found")
            sys.exit(1)

        rows = session.execute(
            project_collaborators.select().where(
                project_collaborators.c.project_id == project.id
            )
        ).fetchall()

        if not rows:
            print(f"No collaborators in project '{project.slug}'")
            return

        print(f"Collaborators of project '{project.slug}':")
        print(f"{'Person ID':<12} {'Name':<30} {'Role':<12}")
        print("-" * 55)
        for row in rows:
            person = session.query(Person).filter(Person.id == row.person_id).first()
            name = person.display_name if person else f"(unknown person {row.person_id})"
            print(f"{row.person_id:<12} {name:<30} {row.role:<12}")


def add_collaborator(args):
    """Add a person as collaborator to a project."""
    with make_session() as session:
        project = session.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Error: Project with ID {args.project_id} not found")
            sys.exit(1)

        person = session.query(Person).filter(Person.id == args.person_id).first()
        if not person:
            print(f"Error: Person with ID {args.person_id} not found")
            sys.exit(1)

        # Check if already exists
        existing = session.execute(
            project_collaborators.select().where(
                project_collaborators.c.project_id == project.id,
                project_collaborators.c.person_id == person.id,
            )
        ).first()

        if existing:
            if args.update:
                session.execute(
                    project_collaborators.update()
                    .where(
                        project_collaborators.c.project_id == project.id,
                        project_collaborators.c.person_id == person.id,
                    )
                    .values(role=args.role)
                )
                session.commit()
                print(f"Updated {person.display_name} role to '{args.role}' in project '{project.slug}'")
            else:
                print("Error: Person already in project. Use --update to change role.")
                sys.exit(1)
            return

        session.execute(
            project_collaborators.insert().values(
                project_id=project.id,
                person_id=person.id,
                role=args.role,
            )
        )
        session.commit()
        print(f"Added {person.display_name} to project '{project.slug}' as {args.role}")


def remove_collaborator(args):
    """Remove a person from a project."""
    with make_session() as session:
        project = session.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Error: Project with ID {args.project_id} not found")
            sys.exit(1)

        person = session.query(Person).filter(Person.id == args.person_id).first()
        if not person:
            print(f"Error: Person with ID {args.person_id} not found")
            sys.exit(1)

        existing = session.execute(
            project_collaborators.select().where(
                project_collaborators.c.project_id == project.id,
                project_collaborators.c.person_id == person.id,
            )
        ).first()

        if not existing:
            print("Error: Person not in project")
            sys.exit(1)

        session.execute(
            project_collaborators.delete().where(
                project_collaborators.c.project_id == project.id,
                project_collaborators.c.person_id == person.id,
            )
        )
        session.commit()
        print(f"Removed {person.display_name} from project '{project.slug}'")


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


def list_unclassified(args):
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
    list_projects_parser = subparsers.add_parser("list-projects", help="List all projects (milestones)")
    list_projects_parser.set_defaults(func=list_projects)

    # list-collaborators
    list_collaborators_parser = subparsers.add_parser("list-collaborators", help="List collaborators of a project")
    list_collaborators_parser.add_argument("--project-id", type=int, required=True, help="Project (milestone) ID")
    list_collaborators_parser.set_defaults(func=list_collaborators)

    # add-collaborator
    add_collaborator_parser = subparsers.add_parser("add-collaborator", help="Add person to project")
    add_collaborator_parser.add_argument("--project-id", type=int, required=True, help="Project (milestone) ID")
    add_collaborator_parser.add_argument("--person-id", type=int, required=True, help="Person ID")
    add_collaborator_parser.add_argument(
        "--role",
        required=True,
        choices=["contributor", "manager", "admin"],
        help="Role in project",
    )
    add_collaborator_parser.add_argument("--update", action="store_true", help="Update existing collaboration")
    add_collaborator_parser.set_defaults(func=add_collaborator)

    # remove-collaborator
    remove_collaborator_parser = subparsers.add_parser("remove-collaborator", help="Remove person from project")
    remove_collaborator_parser.add_argument("--project-id", type=int, required=True, help="Project (milestone) ID")
    remove_collaborator_parser.add_argument("--person-id", type=int, required=True, help="Person ID")
    remove_collaborator_parser.set_defaults(func=remove_collaborator)

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
    classify_parser.add_argument("--project-id", type=int, required=True, help="Project (milestone) ID")
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
