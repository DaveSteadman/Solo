from __future__ import annotations

import argparse
import json
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .web import send_json, serve_bounded_file, serve_file


JsonHandler = Callable[[BaseHTTPRequestHandler, str, dict[str, list[str]], dict[str, Any]], bool]


def parse_service_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument("--host", default=None, help="Bind host.")
    parser.add_argument("--port", type=int, default=None, help="Bind port.")
    parser.add_argument("--dry-run", action="store_true", help="Show configuration without starting the server.")
    return parser.parse_args()


def run_http_service(
    *,
    label: str,
    host: str,
    port: int,
    ui_dir: Path,
    common_ui_dir: Path,
    status_payload: Callable[[], dict[str, Any]],
    route_handler: JsonHandler,
) -> None:
    stop_event = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    import urllib.parse

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query)
            if path in ("", "/", "/ui"):
                serve_file(self, ui_dir / "index.html")
                return
            if path == "/status":
                send_json(self, status_payload())
                return
            if path.startswith("/ui/"):
                relative_path = path.removeprefix("/ui/")
                if "." not in Path(relative_path).name:
                    serve_file(self, ui_dir / "index.html")
                    return
                serve_bounded_file(self, ui_dir, relative_path)
                return
            if path.startswith("/common/"):
                serve_bounded_file(self, common_ui_dir, path.removeprefix("/common/"))
                return
            if route_handler(self, "GET", path, params, {}):
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            self._handle_body("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._handle_body("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle_body("DELETE")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_body(self, method: str) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            payload = self._read_json()
            params = urllib.parse.parse_qs(parsed.query)
            if route_handler(self, method, parsed.path, params, payload):
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.timeout = 0.5
    print(f"{label}: http://{host}:{port}/ui", flush=True)
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()


def query_int(params: dict[str, list[str]], key: str, default: int) -> int:
    values = params.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default


def query_text(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    return str(values[0]) if values else default
