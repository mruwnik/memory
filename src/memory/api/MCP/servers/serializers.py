"""Serialization helpers for project MCP tools.

Pure conversion functions that turn `Project` ORM rows into the JSON-shaped
dicts the MCP tool layer returns. Kept separate from `projects.py` so other
helper modules can import them without circularity.
"""

from typing import Any, cast

from memory.common.db.models import Project


def project_to_dict(
    project: Project,
    include_teams: bool = False,
    include_owner: bool = False,
    children_count: int = 0,
) -> dict[str, Any]:
    """Convert a Project model to a dictionary for API responses."""
    repo_path = None
    if project.repo:
        repo_path = f"{project.repo.owner}/{project.repo.name}"

    result: dict[str, Any] = {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "state": project.state,
        "due_on": project.due_on.isoformat() if project.due_on else None,
        "doc_url": project.doc_url,
        "repo_path": repo_path,
        "github_id": project.github_id,
        "number": project.number,
        "parent_id": project.parent_id,
        "owner_id": project.owner_id,
        "children_count": children_count,
    }

    if include_owner and project.owner:
        result["owner"] = {
            "id": project.owner.id,
            "identifier": project.owner.identifier,
            "display_name": project.owner.display_name,
        }

    if include_teams:
        result["teams"] = [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "member_count": len(t.members) if t.members else None,
            }
            for t in project.teams
        ]

    return result


def build_tree(projects: list[Project]) -> list[dict[str, Any]]:
    """Build a nested tree structure from a flat list of projects."""
    # Build a map of id -> project
    project_map: dict[int, Project] = {cast(int, p.id): p for p in projects}

    # Build a map of parent_id -> children
    # Projects with orphaned parent_id (parent not in project_map) are treated as top-level
    children_map: dict[int | None, list[Project]] = {}
    for p in projects:
        parent = p.parent_id
        # Treat orphaned projects (parent doesn't exist) as top-level
        if parent is not None and parent not in project_map:
            parent = None
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(p)

    def build_subtree(parent_id: int | None) -> list[dict[str, Any]]:
        children = children_map.get(parent_id, [])
        return [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "state": p.state,
                "doc_url": p.doc_url,
                "repo_path": f"{p.repo.owner}/{p.repo.name}" if p.repo else None,
                "parent_id": p.parent_id,
                "children": build_subtree(cast(int, p.id)),
            }
            for p in children
        ]

    return build_subtree(None)
