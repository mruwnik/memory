"""GitHub Projects management mixin."""

from __future__ import annotations
import logging
from typing import Any, Generator, TYPE_CHECKING

from .types import (
    GITHUB_API_URL,
    GithubProjectData,
    GithubProjectFieldDef,
    parse_github_date,
)

if TYPE_CHECKING:
    from .core import GithubClientCore

logger = logging.getLogger(__name__)


class ProjectsMixin(GithubClientCore if TYPE_CHECKING else object):
    """Mixin providing GitHub Projects (v2) methods."""

    # GraphQL fragment for fetching project field DEFINITIONS (schema/metadata)
    _PROJECT_FIELD_DEFS_FRAGMENT = """
    projectsV2(first: 20, query: $projectName) {
      nodes {
        id
        title
        fields(first: 30) {
          nodes {
            ... on ProjectV2Field { id, name, dataType }
            ... on ProjectV2SingleSelectField {
              id
              name
              dataType
              options { id, name }
            }
            ... on ProjectV2IterationField { id, name, dataType }
          }
        }
      }
    }
    """

    def _parse_project_fields(
        self, projects: list[dict[str, Any]], project_name: str
    ) -> dict[str, Any] | None:
        """Parse project list to find matching project and extract fields."""
        for project in projects:
            if project.get("title") == project_name:
                fields: dict[str, Any] = {}
                for field in self._extract_nested(project, "fields", "nodes", default=[]):
                    field_name = field.get("name")
                    if not field_name:
                        continue
                    field_info: dict[str, Any] = {"id": field["id"]}
                    if "dataType" in field:
                        field_info["data_type"] = field["dataType"]
                    if "options" in field:
                        field_info["options"] = {
                            opt["name"]: opt["id"] for opt in field["options"]
                        }
                    fields[field_name] = field_info
                return {"id": project["id"], "fields": fields}
        return None

    def find_project_by_name(
        self, owner: str, project_name: str, is_org: bool = True
    ) -> dict[str, Any] | None:
        """Find a project by name and return its ID and field definitions.

        Returns:
            {
                "id": "project_node_id",
                "fields": {
                    "Status": {"id": "field_id", "options": {"Todo": "option_id", ...}},
                    ...
                }
            }
            or None if not found
        """
        entity_type = "organization" if is_org else "user"
        query = f"""
        query($owner: String!, $projectName: String!) {{
          {entity_type}(login: $owner) {{
            {self._PROJECT_FIELD_DEFS_FRAGMENT}
          }}
        }}
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "projectName": project_name},
            operation_name=f"find_project({project_name})",
        )
        if errors:
            logger.warning(
                f"Error finding project '{project_name}' in {entity_type} '{owner}': {errors}"
            )
            return None
        if data is None:
            return None

        projects = self._extract_nested(data, entity_type, "projectsV2", "nodes", default=[])
        result = self._parse_project_fields(projects, project_name)
        if result is None:
            available = [p.get("title") for p in projects]
            logger.info(
                f"Project '{project_name}' not found in {entity_type} '{owner}'. Available: {available}"
            )
        return result

    def add_issue_to_project(self, project_id: str, content_id: str) -> str | None:
        """Add an issue to a project.

        Args:
            project_id: GraphQL node ID of the project
            content_id: GraphQL node ID of the issue

        Returns:
            Project item ID on success, None on failure
        """
        mutation = """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item { id }
          }
        }
        """
        data, errors = self._graphql(
            mutation,
            {"projectId": project_id, "contentId": content_id},
            operation_name="add_issue_to_project",
        )
        if errors:
            logger.warning(f"Failed to add issue to project: {errors}")
            return None
        if data is None:
            return None
        return self._extract_nested(data, "addProjectV2ItemById", "item", "id")

    def get_project_item_id(
        self, owner: str, repo: str, number: int, project_id: str
    ) -> str | None:
        """Get the project item ID for an issue already in a project."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              projectItems(first: 20) {
                nodes {
                  id
                  project { id }
                }
              }
            }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name="get_project_item_id",
        )
        if errors or data is None:
            return None

        items = self._extract_nested(
            data, "repository", "issue", "projectItems", "nodes", default=[]
        )
        for item in items:
            if self._extract_nested(item, "project", "id") == project_id:
                return item.get("id")
        return None

    def update_project_field_value(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
        value: str,
        value_type: str = "singleSelectOptionId",
    ) -> bool:
        """Update a field value for a project item.

        Args:
            project_id: GraphQL node ID of the project
            item_id: GraphQL node ID of the project item
            field_id: GraphQL node ID of the field
            value: The value to set
            value_type: Type of value - "singleSelectOptionId", "text", "number", "date"

        Returns:
            True on success, False on failure
        """
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
          updateProjectV2ItemFieldValue(
            input: {projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: $value}
          ) {
            projectV2Item { id }
          }
        }
        """
        data, errors = self._graphql(
            mutation,
            {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": field_id,
                "value": {value_type: value},
            },
            operation_name="update_project_field_value",
        )
        return data is not None and errors is None

    def fetch_project(
        self,
        owner: str,
        project_number: int,
        is_org: bool = True,
    ) -> GithubProjectData | None:
        """Fetch a GitHub Project (v2) by owner and project number."""
        entity_type = "organization" if is_org else "user"
        query = f"""
        query($owner: String!, $number: Int!) {{
          {entity_type}(login: $owner) {{
            projectV2(number: $number) {{
              id
              number
              title
              shortDescription
              readme
              public
              closed
              url
              createdAt
              updatedAt
              items(first: 0) {{
                totalCount
              }}
              fields(first: 50) {{
                nodes {{
                  ... on ProjectV2Field {{
                    id
                    name
                    dataType
                  }}
                  ... on ProjectV2SingleSelectField {{
                    id
                    name
                    dataType
                    options {{
                      id
                      name
                    }}
                  }}
                  ... on ProjectV2IterationField {{
                    id
                    name
                    dataType
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "number": project_number},
            operation_name=f"fetch_project({owner}/{project_number})",
        )
        if errors or data is None:
            return None

        project = self._extract_nested(data, entity_type, "projectV2")
        if project is None:
            return None

        # Parse fields
        fields: list[GithubProjectFieldDef] = []
        for field_node in self._extract_nested(project, "fields", "nodes", default=[]):
            field_def = GithubProjectFieldDef(
                id=field_node.get("id", ""),
                name=field_node.get("name", ""),
                data_type=field_node.get("dataType", "TEXT"),
                options=None,
            )
            if "options" in field_node:
                field_def["options"] = {
                    opt["name"]: opt["id"] for opt in field_node["options"]
                }
            fields.append(field_def)

        return GithubProjectData(
            node_id=project.get("id", ""),
            number=project.get("number", project_number),
            title=project.get("title", ""),
            short_description=project.get("shortDescription"),
            readme=project.get("readme"),
            public=project.get("public", False),
            closed=project.get("closed", False),
            owner_type=entity_type,
            owner_login=owner,
            url=project.get("url", ""),
            fields=fields,
            github_created_at=parse_github_date(project.get("createdAt")),
            github_updated_at=parse_github_date(project.get("updatedAt")),
            items_total_count=self._extract_nested(
                project, "items", "totalCount", default=0
            ),
        )

    def list_projects(
        self,
        owner: str,
        is_org: bool = True,
        include_closed: bool = False,
    ) -> Generator[GithubProjectData, None, None]:
        """List all GitHub Projects (v2) for an owner."""
        entity_type = "organization" if is_org else "user"
        query = f"""
        query($owner: String!, $cursor: String) {{
          {entity_type}(login: $owner) {{
            projectsV2(first: 20, after: $cursor) {{
              pageInfo {{
                hasNextPage
                endCursor
              }}
              nodes {{
                id
                number
                title
                shortDescription
                readme
                public
                closed
                url
                createdAt
                updatedAt
                items(first: 0) {{
                  totalCount
                }}
                fields(first: 50) {{
                  nodes {{
                    ... on ProjectV2Field {{
                      id
                      name
                      dataType
                    }}
                    ... on ProjectV2SingleSelectField {{
                      id
                      name
                      dataType
                      options {{
                        id
                        name
                      }}
                    }}
                    ... on ProjectV2IterationField {{
                      id
                      name
                      dataType
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        cursor = None
        while True:
            data, errors = self._graphql(
                query,
                {"owner": owner, "cursor": cursor},
                operation_name=f"list_projects({owner})",
            )
            if errors or data is None:
                return

            projects_data = self._extract_nested(data, entity_type, "projectsV2")
            if projects_data is None:
                return

            for project in self._extract_nested(projects_data, "nodes", default=[]):
                if not include_closed and project.get("closed", False):
                    continue

                # Parse fields
                fields: list[GithubProjectFieldDef] = []
                for field_node in self._extract_nested(
                    project, "fields", "nodes", default=[]
                ):
                    field_def = GithubProjectFieldDef(
                        id=field_node.get("id", ""),
                        name=field_node.get("name", ""),
                        data_type=field_node.get("dataType", "TEXT"),
                        options=None,
                    )
                    if "options" in field_node:
                        field_def["options"] = {
                            opt["name"]: opt["id"] for opt in field_node["options"]
                        }
                    fields.append(field_def)

                yield GithubProjectData(
                    node_id=project.get("id", ""),
                    number=project.get("number", 0),
                    title=project.get("title", ""),
                    short_description=project.get("shortDescription"),
                    readme=project.get("readme"),
                    public=project.get("public", False),
                    closed=project.get("closed", False),
                    owner_type=entity_type,
                    owner_login=owner,
                    url=project.get("url", ""),
                    fields=fields,
                    github_created_at=parse_github_date(project.get("createdAt")),
                    github_updated_at=parse_github_date(project.get("updatedAt")),
                    items_total_count=self._extract_nested(
                        project, "items", "totalCount", default=0
                    ),
                )

            page_info = self._extract_nested(projects_data, "pageInfo", default={})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

    def list_repos(
        self,
        per_page: int = 100,
        sort: str = "updated",
        max_repos: int | None = None,
        include_archived: bool = False,
    ) -> Generator[dict[str, Any], None, None]:
        """List repositories accessible to the authenticated user/app.

        Note: This uses REST API intentionally because:
        1. For PAT auth: The REST `/user/repos?affiliation=owner,collaborator,organization_member`
           endpoint provides all repos in one query. GraphQL would require multiple queries
           (viewer.repositories, viewer.repositoriesContributedTo, viewer.organizations...).
        2. For App auth: There's no direct GraphQL equivalent for `/installation/repositories`.
        3. This method doesn't suffer from N+1 problems - it just lists repos once.

        Args:
            per_page: Number of repos to fetch per API request (max 100).
            sort: Sort order for repos (default: updated).
            max_repos: Maximum number of repos to return. None means no limit (fetch all).
            include_archived: Whether to include archived repos (default: False).
        """
        if self.credentials.auth_type == "app":
            url = f"{GITHUB_API_URL}/installation/repositories"
            params: dict[str, Any] = {"per_page": per_page}
        else:
            url = f"{GITHUB_API_URL}/user/repos"
            params = {
                "per_page": per_page,
                "sort": sort,
                "affiliation": "owner,collaborator,organization_member",
            }

        page = 1
        repos_yielded = 0

        while True:
            params["page"] = page
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            self._handle_rate_limit(response)

            data = response.json()
            repos = data.get("repositories", data) if isinstance(data, dict) else data

            if not repos:
                break

            for repo in repos:
                if max_repos is not None and repos_yielded >= max_repos:
                    return
                if not include_archived and repo.get("archived", False):
                    continue
                yield {
                    "owner": repo["owner"]["login"],
                    "name": repo["name"],
                    "full_name": repo["full_name"],
                    "description": repo.get("description"),
                    "private": repo.get("private", False),
                    "html_url": repo.get("html_url"),
                }
                repos_yielded += 1

            if len(repos) < per_page:
                break

            page += 1
