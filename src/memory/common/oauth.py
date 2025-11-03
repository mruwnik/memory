import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, urljoin

import aiohttp
from memory.common import settings
from memory.common.db.models import MCPServer

logger = logging.getLogger(__name__)


@dataclass
class OAuthEndpoints:
    authorization_endpoint: str
    registration_endpoint: str
    token_endpoint: str
    redirect_uri: str


def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge.

    Returns:
        Tuple of (code_verifier, code_challenge)
    """
    # Generate a random code verifier
    code_verifier = (
        urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")
    )

    # Create code challenge using S256 method
    challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")

    return code_verifier, code_challenge


async def discover_oauth_metadata(server_url: str) -> dict | None:
    """Discover OAuth metadata from an MCP server."""
    # Try the standard OAuth discovery endpoint
    discovery_url = urljoin(server_url, "/.well-known/oauth-authorization-server")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                discovery_url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as exc:
        logger.warning(f"Failed to discover OAuth metadata from {discovery_url}: {exc}")

    return None


async def get_endpoints(url: str) -> OAuthEndpoints:
    # Discover OAuth endpoints from the target server
    oauth_metadata = await discover_oauth_metadata(url)

    if not oauth_metadata:
        raise ValueError(
            "**Failed to connect to MCP server**\n\n"
            f"Could not discover OAuth endpoints at `{url}`\n"
            "Make sure the server is running and supports OAuth 2.0.",
        )

    authorization_endpoint = oauth_metadata.get("authorization_endpoint")
    registration_endpoint = oauth_metadata.get("registration_endpoint")
    token_endpoint = oauth_metadata.get("token_endpoint")

    if not authorization_endpoint:
        raise ValueError(
            "**Invalid OAuth configuration**\n\n"
            f"Server `{url}` did not provide an authorization endpoint.",
        )

    if not registration_endpoint:
        raise ValueError(
            "**Invalid OAuth configuration**\n\n"
            f"Server `{url}` does not support dynamic client registration.",
        )

    if not token_endpoint:
        raise ValueError(
            "**Invalid OAuth configuration**\n\n"
            f"Server `{url}` does not provide a token endpoint.",
        )

    logger.info(f"Authorization endpoint: {authorization_endpoint}")
    logger.info(f"Registration endpoint: {registration_endpoint}")

    return OAuthEndpoints(
        authorization_endpoint=authorization_endpoint,
        registration_endpoint=registration_endpoint,
        token_endpoint=token_endpoint,
        redirect_uri=f"{settings.SERVER_URL}/auth/callback/discord",
    )


async def register_oauth_client(
    endpoints: OAuthEndpoints,
    url: str,
    client_name: str,
) -> None:
    """Register OAuth client and store client_id in the mcp_server object."""
    client_metadata = {
        "client_name": client_name,
        "redirect_uris": [endpoints.redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": "read write",
        "token_endpoint_auth_method": "none",
    }

    logger.error(f"Registration metadata: {client_metadata}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoints.registration_endpoint,
                json=client_metadata,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                logger.error(
                    f"Registration response: {resp.status} {await resp.text()}"
                )
                resp.raise_for_status()
                client_info = await resp.json()
    except Exception as exc:
        raise ValueError(
            f"Failed to register OAuth client at {endpoints.registration_endpoint}: {exc}"
        )

    if not client_info or "client_id" not in client_info:
        raise ValueError(
            "**Failed to register OAuth client**\n\n"
            f"Could not register with the MCP server at `{url}`\n"
            f"Check the server logs for more details.",
        )

    client_id = client_info["client_id"]

    logger.info(f"Registered OAuth client: {client_id}")
    return client_id


async def issue_challenge(
    mcp_server: MCPServer,
    endpoints: OAuthEndpoints,
) -> str:
    """Generate OAuth challenge and store state in mcp_server object."""
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    # Store in mcp_server object
    mcp_server.state = state  # type: ignore
    mcp_server.code_verifier = code_verifier  # type: ignore

    logger.info(
        f"Generated OAuth state for MCP server {mcp_server.mcp_server_url}: "
        f"state={state[:20]}..., verifier={code_verifier[:20]}..."
    )

    # Build authorization URL pointing to the target server
    auth_params = {
        "client_id": mcp_server.client_id,
        "redirect_uri": endpoints.redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": "read write",
    }

    return f"{endpoints.authorization_endpoint}?{urlencode(auth_params)}"


async def complete_oauth_flow(
    mcp_server: MCPServer, code: str, state: str
) -> tuple[int, str]:
    """Complete OAuth flow by exchanging code for token.

    Args:
        code: Authorization code from OAuth callback
        state: State parameter from OAuth callback

    Returns:
        Tuple of (status_code, html_message) for the callback response
    """
    try:
        if not mcp_server:
            logger.error(f"Invalid or expired state: {state[:20]}...")
            return 400, "Invalid or expired OAuth state"

        logger.info(
            f"Found MCP server config: id={mcp_server.id}, "
            f"url={mcp_server.mcp_server_url}"
        )

        # Get OAuth endpoints
        try:
            endpoints = await get_endpoints(str(mcp_server.mcp_server_url))
        except Exception as exc:
            return 500, f"Failed to get OAuth endpoints: {str(exc)}"

        # Exchange authorization code for access token
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": endpoints.redirect_uri,
            "client_id": mcp_server.client_id,
            "code_verifier": mcp_server.code_verifier,
        }

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                endpoints.token_endpoint,
                data=token_data,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Token exchange failed: {resp.status} - {error_text}")
                    return 500, f"Token exchange failed: {error_text}"

                tokens = await resp.json()

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)

        if not access_token:
            return 500, "Token response did not include access_token"

        logger.info(f"Successfully obtained access token: {access_token[:20]}...")

        # Store tokens and clear temporary OAuth state
        mcp_server.access_token = access_token  # type: ignore
        mcp_server.refresh_token = refresh_token  # type: ignore
        mcp_server.token_expires_at = datetime.now() + timedelta(seconds=expires_in)  # type: ignore

        # Clear temporary OAuth flow data
        mcp_server.state = None  # type: ignore
        mcp_server.code_verifier = None  # type: ignore

        logger.info(
            f"Stored tokens for MCP server id={mcp_server.id}, "
            f"url={mcp_server.mcp_server_url}"
        )

        return 200, "✅ Authorization successful! You can now use this MCP server."

    except Exception as exc:
        logger.exception(f"Failed to complete OAuth flow: {exc}")
        return 500, f"Failed to complete OAuth flow: {str(exc)}"
