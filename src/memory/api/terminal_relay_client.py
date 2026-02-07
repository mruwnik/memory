"""Async TCP client for the in-container terminal relay.

Connects directly to the relay server running inside Claude containers,
bypassing docker exec for ~40x faster tmux interaction.
"""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
REQUEST_TIMEOUT = 10.0


class RelayClient:
    """Persistent TCP connection to a container's terminal relay."""

    def __init__(self, host: str, port: int = 9100):
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._writer is not None and not self._writer.is_closing():
            return self._reader, self._writer  # type: ignore[return-value]

        # Close stale connection if any
        await self._close_connection()

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=CONNECT_TIMEOUT,
        )
        return self._reader, self._writer

    async def _close_connection(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            try:
                reader, writer = await self._ensure_connected()
                writer.write(json.dumps(payload).encode() + b"\n")
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=REQUEST_TIMEOUT)
                if not line:
                    raise ConnectionError("relay closed connection")
                return json.loads(line)
            except (OSError, asyncio.TimeoutError, ConnectionError, ValueError, asyncio.LimitOverrunError) as e:
                await self._close_connection()
                raise RelayError(str(e)) from e

    async def capture_screen(self) -> dict[str, Any]:
        return await self._request({"action": "capture"})

    async def send_keys(self, keys: str, literal: bool = True) -> dict[str, Any]:
        return await self._request({"action": "send_keys", "keys": keys, "literal": literal})

    async def resize(self, cols: int, rows: int) -> dict[str, Any]:
        return await self._request({"action": "resize", "cols": cols, "rows": rows})

    async def mouse_scroll(self, direction: str = "down") -> dict[str, Any]:
        return await self._request({"action": "mouse_scroll", "direction": direction})

    async def capture_history(self, start: int = -1000, end: int = -1) -> dict[str, Any]:
        return await self._request({"action": "capture_history", "start": start, "end": end})

    async def ping(self) -> bool:
        try:
            result = await self._request({"action": "ping"})
            return result.get("status") == "ok"
        except RelayError:
            return False

    async def close(self) -> None:
        async with self._lock:
            await self._close_connection()


class RelayError(Exception):
    """Error communicating with the terminal relay."""
