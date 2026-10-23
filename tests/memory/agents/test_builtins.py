import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from memory.agents import builtins
from memory.agents.toolhub import local_web_fetch
from memory.common.safe_http import NetworkPolicy, PinnedBackend
from memory.common.ssrf import UnsafeURLError

PAGES = {
    "/page": ("text/html; charset=utf-8", b"<h1>Title</h1><p>body</p>"),
    "/big": ("text/plain", b"a" * 6 + b"b" * 6 + b"c" * 6),
    "/image": ("image/png", b"\x89PNG"),
}
REDIRECTS = {
    "/to-page": "/page",
    "/to-metadata": "http://169.254.169.254/latest/meta-data/",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in PAGES:
            content_type, body = PAGES[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/encoding":
            body = self.headers.get("Accept-Encoding", "").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/drip-body":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "50")
            self.end_headers()
            self.drip(b"x" * 50)
        elif self.path == "/drip-headers":
            self.drip(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nX-Slow: " + b"y" * 50
            )
        else:
            self.send_error(404)

    def drip(self, data: bytes) -> None:
        for byte in data:
            try:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
            except OSError:
                return
            time.sleep(0.2)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    """A local server web_fetch may reach; every other destination keeps the real policy."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    policy = NetworkPolicy(loopback_allowed=True, public_only=True)
    monkeypatch.setattr(builtins, "fetch_backend", lambda: PinnedBackend(policy=policy))
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.asyncio
async def test_html_is_converted_to_markdown(server):
    assert await builtins.web_fetch(f"{server}/page") == "Title\n=====\n\nbody"


@pytest.mark.asyncio
async def test_redirects_are_followed(server):
    assert await builtins.web_fetch(f"{server}/to-page") == "Title\n=====\n\nbody"


@pytest.mark.asyncio
async def test_redirects_to_internal_targets_are_refused(server):
    with pytest.raises(UnsafeURLError):
        await builtins.web_fetch(f"{server}/to-metadata")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://[fd00::1]/",
        "http://qdrant:6333/collections",
    ],
)
async def test_private_and_stack_targets_are_refused_at_dial_time(url):
    with pytest.raises(UnsafeURLError):
        await builtins.web_fetch(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
async def test_non_http_schemes_are_refused(url):
    with pytest.raises(httpx.UnsupportedProtocol):
        await builtins.web_fetch(url)


@pytest.mark.asyncio
async def test_large_bodies_are_cut_at_the_byte_cap(server, monkeypatch):
    monkeypatch.setattr(builtins, "MAX_FETCH_BYTES", 10)

    text = await builtins.web_fetch(f"{server}/big")

    # The chunk that crosses the cap is dropped whole.
    body, marker = text.rsplit("\n", 1)
    assert marker == "[truncated: page larger than 10 bytes]"
    assert len(body) <= 10
    assert PAGES["/big"][1].decode().startswith(body)


@pytest.mark.asyncio
async def test_unsupported_content_is_not_returned(server):
    assert (
        await builtins.web_fetch(f"{server}/image")
        == "[unsupported content type: image/png]"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path,expected", [("/drip-body", "x"), ("/drip-headers", "")])
async def test_one_deadline_covers_headers_and_body(
    server, monkeypatch, path, expected
):
    monkeypatch.setattr(builtins, "FETCH_DEADLINE_SEC", 1)
    started = time.monotonic()

    text = await builtins.web_fetch(f"{server}{path}")

    assert time.monotonic() - started < 3
    assert text.startswith(expected)
    assert text.endswith("[truncated: page took longer than 1s]")


@pytest.mark.asyncio
async def test_http_failures_are_short_error_results(server):
    text, is_error = await local_web_fetch({"url": f"{server}/missing"})

    assert is_error
    assert text.startswith("fetch failed: Client error '404 Not Found'")


@pytest.mark.asyncio
async def test_malformed_urls_are_short_error_results():
    text, is_error = await local_web_fetch({"url": "http://[::1"})

    assert is_error
    assert text.startswith("fetch failed:")


@pytest.mark.asyncio
async def test_bodies_are_requested_uncompressed_so_the_cap_counts_wire_bytes(server):
    assert await builtins.web_fetch(f"{server}/encoding") == "identity"
