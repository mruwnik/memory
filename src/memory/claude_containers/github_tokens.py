"""API endpoints for the GitHub PATs injected into a user's Claude sessions.

Tokens are write-only: responses carry only their last 4 characters.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import require_scope
from memory.common.db.connection import get_session
from memory.common.db.models import ClaudeGitHubToken, User
from memory.common.db.models.claude_config import GITHUB_TOKEN_ENV_VARS
from memory.common.scopes import SCOPE_CLAUDE_SESSIONS

router = APIRouter(prefix="/claude/github-tokens", tags=["claude-github-tokens"])

TokenKind = Literal["clone", "write"]


class SetTokenRequest(BaseModel):
    token: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_]+$", min_length=20, max_length=255)]


def find_token(db: DBSession, user_id: int, kind: str) -> ClaudeGitHubToken | None:
    return (
        db.query(ClaudeGitHubToken)
        .filter(ClaudeGitHubToken.user_id == user_id, ClaudeGitHubToken.kind == kind)
        .first()
    )


def github_token_env(
    db: DBSession,
    user_id: int,
    requested: dict[str, str | None],
    use_saved_write_token: bool = False,
) -> dict[str, str]:
    """Env vars for the GitHub tokens a spawned session gets.

    A token passed in the spawn request wins over the user's saved token of that
    kind; scheduled sessions never pass one. The saved write token is opt-in
    because it can be a broad PAT, and unattended sessions run prompts that may
    come from repo content.
    """
    stored = {
        t.kind: t.token
        for t in db.query(ClaudeGitHubToken).filter(ClaudeGitHubToken.user_id == user_id)
    }
    env = {}
    for kind, env_var in GITHUB_TOKEN_ENV_VARS.items():
        if token := requested.get(kind):
            env[env_var] = token
        elif kind in stored and (kind != "write" or use_saved_write_token):
            env[env_var] = stored[kind]
    return env


@router.get("")
def list_tokens(
    user: User = require_scope(SCOPE_CLAUDE_SESSIONS),
    db: DBSession = Depends(get_session),
) -> list[dict]:
    tokens = db.query(ClaudeGitHubToken).filter(ClaudeGitHubToken.user_id == user.id)
    return [t.serialize() for t in tokens.order_by(ClaudeGitHubToken.kind)]


@router.put("/{kind}")
def set_token(
    kind: TokenKind,
    data: SetTokenRequest,
    user: User = require_scope(SCOPE_CLAUDE_SESSIONS),
    db: DBSession = Depends(get_session),
) -> dict:
    token = find_token(db, user.id, kind)
    if token is None:
        token = ClaudeGitHubToken(user_id=user.id, kind=kind)
        db.add(token)
    token.token = data.token
    db.commit()
    db.refresh(token)
    return token.serialize()


@router.delete("/{kind}")
def delete_token(
    kind: TokenKind,
    user: User = require_scope(SCOPE_CLAUDE_SESSIONS),
    db: DBSession = Depends(get_session),
) -> dict[str, str]:
    token = find_token(db, user.id, kind)
    if token is None:
        raise HTTPException(status_code=404, detail="No token saved")
    db.delete(token)
    db.commit()
    return {"status": "deleted"}
