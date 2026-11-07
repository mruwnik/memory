"""MCP subserver for GitHub items."""

import asyncio
import logging
from typing import cast

from fastmcp import FastMCP

from memory.api.MCP.access import (
    build_mcp_user_access_filter,
    fetch_accessible_source_items,
    get_mcp_current_user,
    log_item_access,
)
from memory.api.MCP.visibility import READ_ONLY, has_items, require_scopes, visible_when
from memory.common.db.connection import make_session
from memory.common.db.models import GithubPR
from memory.common.github import GithubClient
from memory.common.project.client import account_credentials
from memory.common.scopes import SCOPE_READ

logger = logging.getLogger(__name__)

github_mcp = FastMCP("memory-github")


@github_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ), has_items(GithubPR))
async def pr_diff(id: int) -> str:
    """The full diff of a GitHub PR as it is on GitHub now, unclipped.

    Args:
        id: The PR's item id
    """
    with make_session() as session:
        user = get_mcp_current_user(session)
        access_filter = build_mcp_user_access_filter(session, user)
        prs = fetch_accessible_source_items(
            session, [id], access_filter, base_query=session.query(GithubPR)
        )
        pr = cast(GithubPR, prs[0]) if prs else None
        repo = pr.repo if pr else None
        account = repo.account if repo else None
        if not pr or not repo or not repo.active or not account or not account.active:
            raise ValueError(f"PR {id} not found")
        credentials = account_credentials(account)
        owner, name, number = repo.owner, repo.name, cast(int, pr.number)
        user_id = user.id if user else None

    # App credentials fetch an installation token, so the client is built off the loop too.
    diff = await asyncio.to_thread(
        lambda: GithubClient(credentials).fetch_pr_diff(owner, name, number)
    )
    if user_id is not None:
        try:
            log_item_access(user_id, id)
        except Exception:
            logger.exception("log_item_access failed for user_id=%s item_id=%s", user_id, id)
    if diff is None:
        raise ValueError(f"couldn't fetch the diff of PR {id} from GitHub")
    return diff
