#!/usr/bin/env python3

import argparse

import httpx
import uvicorn
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response


class State(BaseModel):
    email: str
    password: str
    remote_server: str
    session_header: str = "X-Session-ID"
    session_id: str | None = None
    port: int = 8080


def parse_args() -> State:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Simple HTTP proxy with authentication"
    )
    parser.add_argument("--remote-server", required=True, help="Remote server URL")
    parser.add_argument("--email", required=True, help="Email for authentication")
    parser.add_argument("--password", required=True, help="Password for authentication")
    parser.add_argument(
        "--session-header", default="X-Session-ID", help="Session header name"
    )
    parser.add_argument("--port", type=int, default=8080, help="Port to run proxy on")
    return State(**vars(parser.parse_args()))


state = parse_args()


async def login() -> None:
    """Login to remote server and store session ID"""
    login_url = f"{state.remote_server}/auth/login"
    login_data = {"email": state.email, "password": state.password}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(login_url, json=login_data)
            response.raise_for_status()

            login_response = response.json()
            state.session_id = login_response["session_id"]
            print(f"Successfully logged in, session ID: {state.session_id}")
        except httpx.HTTPStatusError as e:
            print(
                f"Login failed with status {e.response.status_code}: {e.response.text}"
            )
            raise
        except Exception as e:
            print(f"Login failed: {e}")
            raise


async def proxy_request(request: Request) -> Response:
    """Proxy request to remote server with session header"""
    if not state.session_id:
        try:
            await login()
        except Exception as e:
            print(f"Login failed: {e}")
            raise HTTPException(status_code=401, detail="Unauthorized")

    # Build the target URL
    target_url = f"{state.remote_server}{request.url.path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    # Get request body
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers | {state.session_header: state.session_id},  # type: ignore
                content=body,
                timeout=30.0,
            )

            # Forward response
            resp = Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type"),
            )
            return resp

        except httpx.RequestError as e:
            print(f"Request failed: {e}")
            raise HTTPException(status_code=502, detail=f"Proxy request failed: {e}")


# Create FastAPI app
app = FastAPI(title="Simple Proxy")


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_all(request: Request):
    """Proxy all requests to remote server"""
    return await proxy_request(request)


if __name__ == "__main__":
    print(f"Starting proxy server on port {state.port}")
    print(f"Proxying to: {state.remote_server}")

    uvicorn.run(app, host="0.0.0.0", port=state.port)
