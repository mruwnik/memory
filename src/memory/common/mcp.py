import json
import logging
import time
from typing import Any, AsyncGenerator

import aiohttp

logger = logging.getLogger(__name__)


async def mcp_call(
    url: str, access_token: str, method: str, params: dict = {}
) -> AsyncGenerator[Any, None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
    }

    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": method,
        "params": params,
    }

    async with aiohttp.ClientSession() as http_session:
        async with http_session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Tools list failed: {resp.status} - {error_text}")
                raise ValueError(
                    f"Failed to call MCP server: {resp.status} - {error_text}"
                )

            # Parse SSE stream
            async for line in resp.content:
                line_str = line.decode("utf-8").strip()

                # SSE format: "data: {json}"
                if line_str.startswith("data: "):
                    json_str = line_str[6:]  # Remove "data: " prefix
                    try:
                        yield json.loads(json_str)
                    except json.JSONDecodeError:
                        continue  # Skip invalid JSON lines


async def mcp_tools_list(url: str, access_token: str) -> list[dict]:
    async for data in mcp_call(url, access_token, "tools/list"):
        if "result" in data and "tools" in data["result"]:
            return data["result"]["tools"]
    return []
