from typing import Annotated
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import AfterValidator

from memory.common.access_control import UserLike
from memory.common.chat_urls import chat_url_allowed


def https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("must be an https:// URL")
    return value.rstrip("/")


# A user-supplied server URL; requests to it still go through safe_http.
HttpsUrl = Annotated[str, AfterValidator(https_url)]


def require_chat_url(url: str, user: UserLike) -> None:
    """400 unless ``user`` may send Slack/Discord traffic to ``url``."""
    if not chat_url_allowed(url, user):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{url} is not an allowed chat API server. Ask an admin to add its "
                "origin to CHAT_API_ALLOWED_ORIGINS."
            ),
        )
