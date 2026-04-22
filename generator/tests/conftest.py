from __future__ import annotations

import contextlib
import functools
import http.server
import socketserver
import threading
from pathlib import Path
from typing import Iterator

import pytest


FIXTURE_APP_DIR = Path(__file__).parent / "fixtures" / "fake_fiori"


class QuietHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextlib.contextmanager
def static_server(directory: Path) -> Iterator[str]:
    handler = functools.partial(QuietHTTPRequestHandler, directory=str(directory))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            yield f"http://{host}:{port}/index.html"
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def fixture_app_url() -> Iterator[str]:
    with static_server(FIXTURE_APP_DIR) as url:
        yield url
