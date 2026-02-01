"""MCP subserver for metadata and utility tools."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, TypedDict, get_args, get_type_hints

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.common import qdrant
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    APIKey,
    APIKeyType,
    EmailAccount,
    SourceItem,
    UserSession,
)

logger = logging.getLogger(__name__)

meta_mcp = FastMCP("memory-meta")


def _get_user_session_from_token(session: DBSession) -> UserSession | None:
    """Get the UserSession from the current access token.

    Returns None if no token or user session found.
    """
    access_token = get_access_token()
    if not access_token:
        return None

    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        return None
    return user_session


def _create_one_time_key(session: DBSession, user_session: UserSession) -> str:
    """Create a one-time API key for the user.

    Returns the key string (only available at creation time).
    The key includes OAuth scopes (read, write) plus the user's MCP tool scopes.
    """
    # Combine OAuth scopes with user's MCP tool scopes
    user_scopes = list(user_session.user.scopes or [])
    scopes = list(set(user_scopes) | {"read", "write"})

    one_time_key = APIKey.create(
        user_id=user_session.user.id,
        key_type=APIKeyType.ONE_TIME,
        name="MCP Client Operation",
        scopes=scopes,
    )
    session.add(one_time_key)
    session.commit()
    return one_time_key.key


def _get_current_user(session: DBSession) -> dict:
    """Get the current authenticated user from the access token."""
    user_session = _get_user_session_from_token(session)
    if not user_session:
        access_token = get_access_token()
        if not access_token:
            return {"authenticated": False}
        return {"authenticated": False, "error": "User not found"}

    user_info = user_session.user.serialize()

    # Add email accounts
    email_accounts = (
        session.query(EmailAccount)
        .filter(
            EmailAccount.user_id == user_session.user.id, EmailAccount.active.is_(True)
        )
        .all()
    )
    user_info["email_accounts"] = [
        {
            "email_address": a.email_address,
            "name": a.name,
            "account_type": a.account_type,
        }
        for a in email_accounts
    ]

    access_token = get_access_token()
    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": access_token.scopes if access_token else [],
        "client_id": access_token.client_id if access_token else None,
        "user": user_info,
        "public_key": user_session.user.ssh_public_key,
    }


# --- Metadata tools ---


class SchemaArg(TypedDict):
    type: str | None
    description: str | None


class CollectionMetadata(TypedDict):
    schema: dict[str, SchemaArg]
    size: int


def from_annotation(annotation: Annotated) -> SchemaArg | None:
    try:
        type_, description = get_args(annotation)
        type_str = str(type_)
        if type_str.startswith("typing."):
            type_str = type_str[7:]
        elif len((parts := type_str.split("'"))) > 1:
            type_str = parts[1]
        return SchemaArg(type=type_str, description=description)
    except IndexError:
        logger.error(f"Error from annotation: {annotation}")
        return None


def get_schema(klass: type[SourceItem]) -> dict[str, SchemaArg]:
    if not hasattr(klass, "as_payload"):
        return {}

    if not (payload_type := get_type_hints(klass.as_payload).get("return")):
        return {}

    return {
        name: schema
        for name, arg in payload_type.__annotations__.items()
        if (schema := from_annotation(arg))
    }


@meta_mcp.tool()
async def get_metadata_schemas() -> dict[str, CollectionMetadata]:
    """Get the metadata schema for each collection used in the knowledge base.

    These schemas can be used to filter the knowledge base.

    Returns: A mapping of collection names to their metadata schemas with field types and descriptions.

    Example:
    ```
    {
        "mail": {"subject": {"type": "str", "description": "The subject of the email."}},
        "chat": {"subject": {"type": "str", "description": "The subject of the chat message."}}
    }
    """
    client = qdrant.get_qdrant_client()
    sizes = qdrant.get_collection_sizes(client)
    schemas = defaultdict(dict)
    for klass in SourceItem.__subclasses__():
        for collection in klass.get_collections():
            schemas[collection].update(get_schema(klass))

    return {
        collection: CollectionMetadata(schema=schema, size=size)
        for collection, schema in schemas.items()
        if (size := sizes.get(collection))
    }


@meta_mcp.tool()
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool()
async def get_user(generate_one_time_key: bool = False) -> dict:
    """Get information about the authenticated user.

    Args:
        generate_one_time_key: If True, generates a one-time API key for client operations.
                               The key will be deleted after first use.
    """
    with make_session() as session:
        result = _get_current_user(session)

        if generate_one_time_key and result.get("authenticated"):
            if user_session := _get_user_session_from_token(session):
                result["one_time_key"] = _create_one_time_key(session, user_session)

        return result
