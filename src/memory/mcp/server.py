import argparse
import logging
from typing import Any
from fastapi import UploadFile
import httpx
from mcp.server.fastmcp import FastMCP

SERVER = "http://localhost:8000"


logger = logging.getLogger(__name__)
mcp = FastMCP("memory")


async def make_request(
    path: str,
    method: str,
    data: dict | None = None,
    json: dict | None = None,
    files: list[UploadFile] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.request(
            method, f"{SERVER}/{path}", data=data, json=json, files=files
        )


async def post_data(path: str, data: dict | None = None) -> httpx.Response:
    return await make_request(path, "POST", data=data)


@mcp.tool()
async def search(
    query: str, previews: bool = False, modalities: list[str] = [], limit: int = 10
) -> list[dict[str, Any]]:
    logger.error(f"Searching for {query}")
    resp = await post_data(
        "search",
        {
            "query": query,
            "previews": previews,
            "modalities": modalities,
            "limit": limit,
        },
    )

    return resp.json()


if __name__ == "__main__":
    # Initialize and run the server
    args = argparse.ArgumentParser()
    args.add_argument("--server", type=str)
    args = args.parse_args()

    SERVER = args.server

    mcp.run(transport=args.transport)
