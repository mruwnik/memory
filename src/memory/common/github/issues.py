"""GitHub issue and PR fetching mixin."""

import logging
from datetime import datetime
from typing import Any, Generator

from .types import (
    GITHUB_API_URL,
    GithubComment,
    GithubFileChange,
    GithubIssueData,
    GithubMilestoneData,
    GithubPRDataDict,
    GithubReview,
    GithubReviewComment,
    compute_content_hash,
    parse_github_date,
)

logger = logging.getLogger(__name__)


class IssuesMixin:
    """Mixin providing issue and PR fetching methods."""

    # GraphQL fragment for fetching project item field VALUES (actual data)
    _PROJECT_ITEM_VALUES_FRAGMENT = """
    fragment ProjectFieldValues on ProjectV2ItemConnection {
      nodes {
        project { title }
        fieldValues(first: 20) {
          nodes {
            ... on ProjectV2ItemFieldTextValue {
              text
              field { ... on ProjectV2Field { name } }
            }
            ... on ProjectV2ItemFieldNumberValue {
              number
              field { ... on ProjectV2Field { name } }
            }
            ... on ProjectV2ItemFieldDateValue {
              date
              field { ... on ProjectV2Field { name } }
            }
            ... on ProjectV2ItemFieldSingleSelectValue {
              name
              field { ... on ProjectV2SingleSelectField { name } }
            }
            ... on ProjectV2ItemFieldIterationValue {
              title
              field { ... on ProjectV2IterationField { name } }
            }
          }
        }
      }
    }
    """

    # GraphQL fragment for issue fields
    _ISSUE_FRAGMENT = """
        number
        title
        body
        state
        author { login }
        labels(first: 100) { nodes { name } }
        assignees(first: 50) { nodes { login } }
        milestone { number }
        createdAt
        closedAt
        updatedAt
        comments(first: 100) {
          nodes {
            databaseId
            author { login }
            body
            createdAt
            updatedAt
          }
          pageInfo { hasNextPage endCursor }
        }
    """

    # GraphQL fragment for PR fields (includes issue fields + PR-specific)
    _PR_FRAGMENT = """
        number
        title
        body
        state
        author { login }
        labels(first: 100) { nodes { name } }
        assignees(first: 50) { nodes { login } }
        milestone { number }
        createdAt
        closedAt
        mergedAt
        updatedAt
        additions
        deletions
        changedFiles
        comments(first: 100) {
          nodes {
            databaseId
            author { login }
            body
            createdAt
            updatedAt
          }
          pageInfo { hasNextPage endCursor }
        }
        reviews(first: 100) {
          nodes {
            databaseId
            author { login }
            state
            body
            submittedAt
          }
        }
        reviewThreads(first: 100) {
          nodes {
            comments(first: 50) {
              nodes {
                databaseId
                author { login }
                body
                path
                line
                diffHunk
                createdAt
              }
            }
          }
        }
        files(first: 100) {
          nodes {
            path
            additions
            deletions
            changeType
          }
          pageInfo { hasNextPage endCursor }
        }
    """

    def fetch_issues(
        self,
        owner: str,
        repo: str,
        since: datetime | None = None,
        state: str = "all",
        labels: list[str] | None = None,
    ) -> Generator[GithubIssueData, None, None]:
        """Fetch issues from a repository with pagination via GraphQL.

        This uses a single GraphQL query per page to fetch issues with all their
        comments, eliminating the N+1 API calls problem of the REST approach.
        """
        # Map state to GraphQL enum
        states = None
        if state == "open":
            states = ["OPEN"]
        elif state == "closed":
            states = ["CLOSED"]
        # state="all" means no filter

        # Build label filter
        label_filter = ""
        if labels:
            label_list = ", ".join(f'"{l}"' for l in labels)
            label_filter = f", labels: [{label_list}]"

        cursor = None
        while True:
            # Build pagination
            after_clause = f', after: "{cursor}"' if cursor else ""

            # Build filter clause
            filter_clause = (
                f'filterBy: {{since: "{since.isoformat()}"}}' if since else ""
            )
            if states:
                state_filter = f"states: [{states[0]}]"
                if filter_clause:
                    filter_clause = f"{filter_clause}, {state_filter}"
                else:
                    filter_clause = state_filter

            query = f"""
            query($owner: String!, $repo: String!) {{
              repository(owner: $owner, name: $repo) {{
                issues(first: 50, orderBy: {{field: UPDATED_AT, direction: DESC}}{after_clause}{label_filter}{", " + filter_clause if filter_clause else ""}) {{
                  nodes {{
                    {self._ISSUE_FRAGMENT}
                  }}
                  pageInfo {{
                    hasNextPage
                    endCursor
                  }}
                }}
              }}
            }}
            """

            data, errors = self._graphql(
                query,
                {"owner": owner, "repo": repo},
                operation_name=f"fetch_issues({owner}/{repo})",
            )
            if errors or data is None:
                logger.warning(f"GraphQL fetch_issues failed: {errors}")
                return

            issues_data = self._extract_nested(data, "repository", "issues")
            if not issues_data:
                return

            nodes = issues_data.get("nodes", [])
            if not nodes:
                return

            for issue in nodes:
                if issue is None:
                    continue
                yield self._parse_issue_graphql(issue)

            page_info = issues_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")

    def fetch_prs(
        self,
        owner: str,
        repo: str,
        since: datetime | None = None,
        state: str = "all",
    ) -> Generator[GithubIssueData, None, None]:
        """Fetch pull requests from a repository with pagination via GraphQL.

        This uses a single GraphQL query per page to fetch PRs with all their
        comments, reviews, review comments, and file changes - eliminating the
        5+ API calls per PR that the REST approach required.

        Note: The full diff still requires a separate REST call with special
        Accept header, but this is fetched lazily only when needed.
        """
        # Map state to GraphQL enum
        states = None
        if state == "open":
            states = ["OPEN"]
        elif state == "closed":
            states = ["CLOSED", "MERGED"]

        cursor = None
        while True:
            after_clause = f', after: "{cursor}"' if cursor else ""
            states_clause = f", states: [{', '.join(states)}]" if states else ""

            query = f"""
            query($owner: String!, $repo: String!) {{
              repository(owner: $owner, name: $repo) {{
                pullRequests(first: 20, orderBy: {{field: UPDATED_AT, direction: DESC}}{after_clause}{states_clause}) {{
                  nodes {{
                    {self._PR_FRAGMENT}
                  }}
                  pageInfo {{
                    hasNextPage
                    endCursor
                  }}
                }}
              }}
            }}
            """

            data, errors = self._graphql(
                query,
                {"owner": owner, "repo": repo},
                operation_name=f"fetch_prs({owner}/{repo})",
                timeout=60,  # PRs can be larger
            )
            if errors or data is None:
                logger.warning(f"GraphQL fetch_prs failed: {errors}")
                return

            prs_data = self._extract_nested(data, "repository", "pullRequests")
            if not prs_data:
                return

            nodes = prs_data.get("nodes", [])
            if not nodes:
                return

            for pr in nodes:
                if pr is None:
                    continue

                # Check since filter (GraphQL doesn't have filterBy for PRs)
                if since:
                    updated_at = parse_github_date(pr.get("updatedAt"))
                    if updated_at and updated_at < since:
                        return  # Stop iteration - PRs are ordered by updated desc

                yield self._parse_pr_graphql(owner, repo, pr)

            page_info = prs_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")

    def _parse_issue_graphql(self, issue: dict[str, Any]) -> GithubIssueData:
        """Parse GraphQL issue response into GithubIssueData."""
        raw_comments = self._extract_nested(issue, "comments", "nodes", default=[])
        comments = [
            GithubComment(
                id=c.get("databaseId", 0),
                author=self._extract_nested(c, "author", "login", default="ghost"),
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                updated_at=c.get("updatedAt", ""),
            )
            for c in raw_comments
            if c is not None
        ]

        body = issue.get("body") or ""
        return GithubIssueData(
            kind="issue",
            number=issue["number"],
            title=issue["title"],
            body=body,
            state=issue["state"].lower(),  # GraphQL returns OPEN/CLOSED
            author=self._extract_nested(issue, "author", "login", default="ghost"),
            labels=[
                label["name"]
                for label in self._extract_nested(issue, "labels", "nodes", default=[])
                if label is not None
            ],
            assignees=[
                a["login"]
                for a in self._extract_nested(issue, "assignees", "nodes", default=[])
                if a is not None
            ],
            milestone_number=self._extract_nested(issue, "milestone", "number"),
            created_at=parse_github_date(issue["createdAt"]),  # type: ignore
            closed_at=parse_github_date(issue.get("closedAt")),
            merged_at=None,
            github_updated_at=parse_github_date(issue["updatedAt"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=None,
            project_fields=None,
            content_hash=compute_content_hash(body, comments),
            pr_data=None,
        )

    def _parse_pr_graphql(
        self, owner: str, repo: str, pr: dict[str, Any]
    ) -> GithubIssueData:
        """Parse GraphQL PR response into GithubIssueData."""
        pr_number = pr["number"]

        # Parse regular comments
        raw_comments = self._extract_nested(pr, "comments", "nodes", default=[])
        comments = [
            GithubComment(
                id=c.get("databaseId", 0),
                author=self._extract_nested(c, "author", "login", default="ghost"),
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                updated_at=c.get("updatedAt", ""),
            )
            for c in raw_comments
            if c is not None
        ]

        # Parse reviews
        raw_reviews = self._extract_nested(pr, "reviews", "nodes", default=[])
        reviews = [
            GithubReview(
                id=r.get("databaseId", 0),
                user=self._extract_nested(r, "author", "login", default="ghost"),
                state=r.get("state", "COMMENTED").lower(),
                body=r.get("body"),
                submitted_at=r.get("submittedAt", ""),
            )
            for r in raw_reviews
            if r is not None
        ]

        # Parse review comments from review threads
        review_comments: list[GithubReviewComment] = []
        raw_threads = self._extract_nested(pr, "reviewThreads", "nodes", default=[])
        for thread in raw_threads:
            if thread is None:
                continue
            thread_comments = self._extract_nested(
                thread, "comments", "nodes", default=[]
            )
            for c in thread_comments:
                if c is None:
                    continue
                review_comments.append(
                    GithubReviewComment(
                        id=c.get("databaseId", 0),
                        user=self._extract_nested(
                            c, "author", "login", default="ghost"
                        ),
                        body=c.get("body", ""),
                        path=c.get("path", ""),
                        line=c.get("line"),
                        side="RIGHT",  # GraphQL doesn't expose side directly
                        diff_hunk=c.get("diffHunk", ""),
                        created_at=c.get("createdAt", ""),
                    )
                )

        # Parse files
        raw_files = self._extract_nested(pr, "files", "nodes", default=[])
        files = [
            GithubFileChange(
                filename=f.get("path", ""),
                status=self._map_change_type(f.get("changeType", "MODIFIED")),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=None,  # GraphQL doesn't provide patches, use REST for diff
            )
            for f in raw_files
            if f is not None
        ]

        # Fetch full diff via REST (requires special Accept header)
        full_diff = self.fetch_pr_diff(owner, repo, pr_number)
        diff_summary = full_diff[:5000] if full_diff else None

        body = pr.get("body") or ""
        pr_data = GithubPRDataDict(
            diff=full_diff,
            files=files,
            additions=pr.get("additions", 0),
            deletions=pr.get("deletions", 0),
            changed_files_count=pr.get("changedFiles", len(files)),
            reviews=reviews,
            review_comments=review_comments,
        )

        return GithubIssueData(
            kind="pr",
            number=pr_number,
            title=pr["title"],
            body=body,
            state=self._map_pr_state(pr["state"]),
            author=self._extract_nested(pr, "author", "login", default="ghost"),
            labels=[
                label["name"]
                for label in self._extract_nested(pr, "labels", "nodes", default=[])
                if label is not None
            ],
            assignees=[
                a["login"]
                for a in self._extract_nested(pr, "assignees", "nodes", default=[])
                if a is not None
            ],
            milestone_number=self._extract_nested(pr, "milestone", "number"),
            created_at=parse_github_date(pr["createdAt"]),  # type: ignore
            closed_at=parse_github_date(pr.get("closedAt")),
            merged_at=parse_github_date(pr.get("mergedAt")),
            github_updated_at=parse_github_date(pr["updatedAt"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=diff_summary,
            project_fields=None,
            content_hash=compute_content_hash(body, comments),
            pr_data=pr_data,
        )

    def _map_change_type(self, change_type: str) -> str:
        """Map GraphQL PullRequestFileChangeType to REST status."""
        mapping = {
            "ADDED": "added",
            "DELETED": "removed",
            "MODIFIED": "modified",
            "RENAMED": "renamed",
            "COPIED": "copied",
            "CHANGED": "modified",
        }
        return mapping.get(change_type, "modified")

    def _map_pr_state(self, state: str) -> str:
        """Map GraphQL PR state to REST-style state."""
        # GraphQL uses OPEN, CLOSED, MERGED
        # REST uses open, closed (merged is closed with merged_at set)
        if state == "MERGED":
            return "closed"
        return state.lower()

    def fetch_pr_diff(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> str | None:
        """Fetch the full diff for a PR (not truncated)."""
        try:
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={"Accept": "application/vnd.github.diff"},
                timeout=60,  # Longer timeout for large diffs
            )
            if response.ok:
                return response.text
        except Exception as e:
            logger.warning(f"Failed to fetch PR diff: {e}")
        return None

    # GraphQL fragment for milestone fields
    _MILESTONE_FRAGMENT = """
        id
        number
        title
        description
        state
        dueOn
        createdAt
        updatedAt
        closedAt
    """

    def fetch_milestones(
        self,
        owner: str,
        repo: str,
        state: str = "all",
    ) -> Generator[GithubMilestoneData, None, None]:
        """Fetch all milestones for a repository via GraphQL."""
        # Map state to GraphQL enum
        states_clause = ""
        if state == "open":
            states_clause = ", states: [OPEN]"
        elif state == "closed":
            states_clause = ", states: [CLOSED]"

        cursor = None
        while True:
            after_clause = f', after: "{cursor}"' if cursor else ""

            query = f"""
            query($owner: String!, $repo: String!) {{
              repository(owner: $owner, name: $repo) {{
                milestones(first: 50, orderBy: {{field: DUE_DATE, direction: ASC}}{after_clause}{states_clause}) {{
                  nodes {{
                    {self._MILESTONE_FRAGMENT}
                  }}
                  pageInfo {{
                    hasNextPage
                    endCursor
                  }}
                }}
              }}
            }}
            """

            data, errors = self._graphql(
                query,
                {"owner": owner, "repo": repo},
                operation_name=f"fetch_milestones({owner}/{repo})",
            )
            if errors or data is None:
                logger.warning(f"GraphQL fetch_milestones failed: {errors}")
                return

            milestones_data = self._extract_nested(data, "repository", "milestones")
            if not milestones_data:
                return

            nodes = milestones_data.get("nodes", [])
            if not nodes:
                return

            for ms in nodes:
                if ms is None:
                    continue
                yield self._parse_milestone_graphql(ms)

            page_info = milestones_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")

    def fetch_milestone(
        self,
        owner: str,
        repo: str,
        milestone_number: int,
    ) -> GithubMilestoneData | None:
        """Fetch a single milestone by number via GraphQL."""
        query = f"""
        query($owner: String!, $repo: String!, $number: Int!) {{
          repository(owner: $owner, name: $repo) {{
            milestone(number: $number) {{
              {self._MILESTONE_FRAGMENT}
            }}
          }}
        }}
        """

        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": milestone_number},
            operation_name=f"fetch_milestone({owner}/{repo}#{milestone_number})",
        )
        if errors or data is None:
            return None

        ms = self._extract_nested(data, "repository", "milestone")
        if ms is None:
            return None

        return self._parse_milestone_graphql(ms)

    def _parse_milestone_graphql(self, ms: dict[str, Any]) -> GithubMilestoneData:
        """Parse GraphQL milestone response into GithubMilestoneData."""
        # GraphQL id is the node ID (string), we need databaseId for github_id
        # But milestones don't expose databaseId in GraphQL, so we use 0
        return GithubMilestoneData(
            github_id=0,  # GraphQL doesn't expose databaseId for milestones
            number=ms["number"],
            title=ms["title"],
            description=ms.get("description"),
            state=ms["state"].lower(),  # GraphQL returns OPEN/CLOSED
            due_on=parse_github_date(ms.get("dueOn")),
            github_created_at=parse_github_date(ms["createdAt"]),  # type: ignore
            github_updated_at=parse_github_date(ms["updatedAt"]),  # type: ignore
            closed_at=parse_github_date(ms.get("closedAt")),
        )

    def _parse_project_items(
        self, items: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Parse project items into a field values dict."""
        if not items:
            return None

        fields: dict[str, Any] = {}
        for item in items:
            project_name = self._extract_nested(
                item, "project", "title", default="unknown"
            )
            for field_value in self._extract_nested(
                item, "fieldValues", "nodes", default=[]
            ):
                field_name = self._extract_nested(field_value, "field", "name")
                if not field_name:
                    continue

                # Extract value based on type
                value = (
                    field_value.get("text")
                    or field_value.get("number")
                    or field_value.get("date")
                    or field_value.get("name")  # Single select
                    or field_value.get("title")  # Iteration
                )

                if value is not None:
                    fields[f"{project_name}.{field_name}"] = value

        return fields if fields else None

    def _fetch_item_project_fields(
        self,
        owner: str,
        repo: str,
        number: int,
        kind: str,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for an issue or PR."""
        query = f"""
        query($owner: String!, $repo: String!, $number: Int!) {{
          repository(owner: $owner, name: $repo) {{
            {kind}(number: $number) {{
              projectItems(first: 10) {{
                ...ProjectFieldValues
              }}
            }}
          }}
        }}
        {self._PROJECT_ITEM_VALUES_FRAGMENT}
        """

        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name=f"fetch_{kind}_project_fields",
        )
        if errors or data is None:
            return None

        items = self._extract_nested(
            data, "repository", kind, "projectItems", "nodes", default=[]
        )
        return self._parse_project_items(items)

    def fetch_project_fields(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for an issue."""
        return self._fetch_item_project_fields(owner, repo, issue_number, "issue")

    def fetch_pr_project_fields(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for a PR."""
        return self._fetch_item_project_fields(owner, repo, pr_number, "pullRequest")

    # =========================================================================
    # GraphQL Methods for Issue Creation/Update
    # =========================================================================

    def get_repository_id(self, owner: str, repo: str) -> str | None:
        """Get the GraphQL node ID for a repository."""
        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) { id }
        }
        """
        data, errors = self._graphql(
            query, {"owner": owner, "repo": repo}, operation_name="get_repository_id"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "repository", "id")

    def get_issue_node_id(self, owner: str, repo: str, number: int) -> str | None:
        """Get the GraphQL node ID for an issue (needed for mutations)."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) { id }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name="get_issue_node_id",
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "repository", "issue", "id")

    def item_exists(self, owner: str, repo: str, number: int, kind: str) -> bool:
        """Check if an issue or PR exists in a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            number: Issue or PR number
            kind: Item type ('issue' or 'pr')

        Returns:
            True if the item exists, False otherwise
        """
        if kind == "pr":
            query = """
            query($owner: String!, $repo: String!, $number: Int!) {
              repository(owner: $owner, name: $repo) {
                pullRequest(number: $number) { id }
              }
            }
            """
            extract_path = ("repository", "pullRequest", "id")
        else:
            # Default to issue query
            query = """
            query($owner: String!, $repo: String!, $number: Int!) {
              repository(owner: $owner, name: $repo) {
                issue(number: $number) { id }
              }
            }
            """
            extract_path = ("repository", "issue", "id")

        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name=f"check_{kind}_exists",
        )
        if errors or data is None:
            return False
        return self._extract_nested(data, *extract_path) is not None

    def items_exist(
        self,
        owner: str,
        repo: str,
        items: list[tuple[int, str]],
    ) -> dict[tuple[int, str], bool]:
        """Check if multiple issues/PRs exist in a single GraphQL query.

        Uses GraphQL aliasing to batch multiple existence checks efficiently.

        Args:
            owner: Repository owner
            repo: Repository name
            items: List of (number, kind) tuples where kind is 'issue' or 'pr'

        Returns:
            Dict mapping each (number, kind) to True if exists, False otherwise
        """
        if not items:
            return {}

        # Build aliased query fields for each item
        fields = []
        for number, kind in items:
            alias = f"item_{number}_{kind}"
            if kind == "pr":
                fields.append(f"{alias}: pullRequest(number: {number}) {{ id }}")
            else:
                fields.append(f"{alias}: issue(number: {number}) {{ id }}")

        query = f"""
        query($owner: String!, $repo: String!) {{
          repository(owner: $owner, name: $repo) {{
            {chr(10).join(fields)}
          }}
        }}
        """

        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo},
            operation_name="check_items_exist_batch",
        )

        results: dict[tuple[int, str], bool] = {}
        repo_data = self._extract_nested(data, "repository") or {}

        for number, kind in items:
            alias = f"item_{number}_{kind}"
            item_data = repo_data.get(alias)
            results[(number, kind)] = item_data is not None and item_data.get("id") is not None

        return results

    def get_label_ids(self, owner: str, repo: str, label_names: list[str]) -> list[str]:
        """Resolve label names to GraphQL node IDs."""
        if not label_names:
            return []

        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            labels(first: 100) {
              nodes { id, name }
            }
          }
        }
        """
        data, errors = self._graphql(
            query, {"owner": owner, "repo": repo}, operation_name="get_label_ids"
        )
        if errors or data is None:
            return []

        labels = self._extract_nested(data, "repository", "labels", "nodes", default=[])
        label_map = {label["name"]: label["id"] for label in labels}
        return [label_map[name] for name in label_names if name in label_map]

    def get_user_id(self, username: str) -> str | None:
        """Get the GraphQL node ID for a user."""
        query = """
        query($login: String!) {
          user(login: $login) { id }
        }
        """
        data, errors = self._graphql(
            query, {"login": username}, operation_name=f"get_user_id({username})"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "user", "id")

    def get_user_ids(self, usernames: list[str]) -> list[str]:
        """Resolve usernames to GraphQL node IDs."""
        if not usernames:
            return []
        return [uid for u in usernames if (uid := self.get_user_id(u))]

    def get_milestone_node_id(
        self, owner: str, repo: str, milestone_number: int
    ) -> str | None:
        """Get the GraphQL node ID for a milestone."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            milestone(number: $number) { id }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": milestone_number},
            operation_name="get_milestone_node_id",
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "repository", "milestone", "id")

    def find_milestone_by_title(
        self, owner: str, repo: str, title: str
    ) -> str | None:
        """Find a milestone by title and return its GraphQL node ID.

        Queries GitHub directly - useful when repo isn't tracked locally.
        """
        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            milestones(first: 100, states: [OPEN, CLOSED]) {
              nodes { id, title }
            }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo},
            operation_name=f"find_milestone_by_title({title})",
        )
        if errors or data is None:
            return None

        milestones = self._extract_nested(
            data, "repository", "milestones", "nodes", default=[]
        )
        for ms in milestones:
            if ms and ms.get("title") == title:
                return ms.get("id")
        return None

    def fetch_issue_graphql(
        self, owner: str, repo: str, number: int
    ) -> GithubIssueData | None:
        """Fetch complete issue data via GraphQL."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              number
              title
              body
              state
              author { login }
              labels(first: 100) { nodes { name } }
              assignees(first: 50) { nodes { login } }
              milestone { number }
              createdAt
              closedAt
              updatedAt
              comments(first: 100) {
                nodes {
                  databaseId
                  author { login }
                  body
                  createdAt
                  updatedAt
                }
              }
            }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name=f"fetch_issue({owner}/{repo}#{number})",
        )
        if errors or data is None:
            return None

        issue = self._extract_nested(data, "repository", "issue")
        if issue is None:
            return None

        # Parse comments
        raw_comments = self._extract_nested(issue, "comments", "nodes", default=[])
        comments = [
            GithubComment(
                id=c.get("databaseId", 0),
                author=self._extract_nested(c, "author", "login", default="ghost"),
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                updated_at=c.get("updatedAt", ""),
            )
            for c in raw_comments
        ]

        body = issue.get("body") or ""
        return GithubIssueData(
            kind="issue",
            number=issue["number"],
            title=issue["title"],
            body=body,
            state=issue["state"].lower(),  # GraphQL returns OPEN/CLOSED
            author=self._extract_nested(issue, "author", "login", default="ghost"),
            labels=[
                label["name"]
                for label in self._extract_nested(issue, "labels", "nodes", default=[])
            ],
            assignees=[
                a["login"]
                for a in self._extract_nested(issue, "assignees", "nodes", default=[])
            ],
            milestone_number=self._extract_nested(issue, "milestone", "number"),
            created_at=parse_github_date(issue["createdAt"]),  # type: ignore
            closed_at=parse_github_date(issue.get("closedAt")),
            merged_at=None,
            github_updated_at=parse_github_date(issue["updatedAt"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=None,
            project_fields=None,
            content_hash=compute_content_hash(body, comments),
            pr_data=None,
        )

    def create_issue_graphql(
        self,
        repository_id: str,
        title: str,
        body: str | None = None,
        label_ids: list[str] | None = None,
        assignee_ids: list[str] | None = None,
        milestone_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new issue using GraphQL mutation."""
        mutation = """
        mutation CreateIssue($input: CreateIssueInput!) {
          createIssue(input: $input) {
            issue { id, number, url, title, state }
          }
        }
        """
        input_data: dict[str, Any] = {"repositoryId": repository_id, "title": title}
        if body is not None:
            input_data["body"] = body
        if label_ids:
            input_data["labelIds"] = label_ids
        if assignee_ids:
            input_data["assigneeIds"] = assignee_ids
        if milestone_id:
            input_data["milestoneId"] = milestone_id

        data, errors = self._graphql(
            mutation, {"input": input_data}, operation_name="create_issue"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "createIssue", "issue")

    def update_issue_graphql(
        self,
        issue_id: str,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        label_ids: list[str] | None = None,
        assignee_ids: list[str] | None = None,
        milestone_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing issue using GraphQL mutation."""
        mutation = """
        mutation UpdateIssue($input: UpdateIssueInput!) {
          updateIssue(input: $input) {
            issue { id, number, url, title, state }
          }
        }
        """
        input_data: dict[str, Any] = {"id": issue_id}
        if title is not None:
            input_data["title"] = title
        if body is not None:
            input_data["body"] = body
        if state is not None:
            input_data["state"] = state.upper()
        if label_ids is not None:
            input_data["labelIds"] = label_ids
        if assignee_ids is not None:
            input_data["assigneeIds"] = assignee_ids
        if milestone_id is not None:
            input_data["milestoneId"] = milestone_id

        data, errors = self._graphql(
            mutation, {"input": input_data}, operation_name="update_issue"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "updateIssue", "issue")

    def add_issue_comment(
        self,
        issue_id: str,
        body: str,
    ) -> dict[str, Any] | None:
        """Add a comment to an issue using GraphQL mutation.

        Args:
            issue_id: GraphQL node ID of the issue (from get_issue_node_id)
            body: Comment body text (markdown supported)

        Returns:
            Dict with comment data (id, url, body, author) or None on failure
        """
        mutation = """
        mutation AddComment($input: AddCommentInput!) {
          addComment(input: $input) {
            commentEdge {
              node {
                id
                databaseId
                url
                body
                author { login }
                createdAt
              }
            }
          }
        }
        """
        input_data = {"subjectId": issue_id, "body": body}

        data, errors = self._graphql(
            mutation, {"input": input_data}, operation_name="add_issue_comment"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "addComment", "commentEdge", "node")
