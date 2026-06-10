from __future__ import annotations

import json
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix in (".ttf", ".otf", ".woff", ".woff2"):
        return "font/ttf"
    return "application/octet-stream"


def build_handler(manager: Any, stop_event: threading.Event, ui_dir: Path, common_ui_dir: Path):
    class HubHandler(BaseHTTPRequestHandler):
        manager_ref: ClassVar[Any] = manager
        stop_ref: ClassVar[threading.Event] = stop_event

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path in ("", "/", "/ui"):
                self._serve_file(ui_dir / "index.html")
                return
            if path == "/report":
                self._send_text(self.manager_ref.report_text())
                return
            if path == "/status":
                self._send_json({"status": "ok", "service": "SoloHub"})
                return
            if path == "/api/snapshot":
                self._send_json(self.manager_ref.snapshot())
                return
            if path == "/api/reports/solostate":
                self._send_json(self.manager_ref.solo_state_report())
                return
            if path.startswith("/api/ui-state/"):
                key = path.removeprefix("/api/ui-state/")
                self._send_json({"key": key, "value": self.manager_ref.get_ui_state(key)})
                return
            if path.startswith("/ui/"):
                self._serve_bounded(ui_dir, path.removeprefix("/ui/"))
                return
            if path.startswith("/common/"):
                self._serve_bounded(common_ui_dir, path.removeprefix("/common/"))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/services/start-auto":
                self._send_json({"ok": True, "changed": self.manager_ref.start_auto()})
                return
            if path == "/api/services/stop-all":
                self._send_json({"ok": True, "changed": self.manager_ref.stop_all()})
                return
            if path == "/api/shutdown":
                self.stop_ref.set()
                self._send_json({"ok": True})
                return
            if path.startswith("/api/ui-state/"):
                key = path.removeprefix("/api/ui-state/")
                try:
                    payload = self._read_json_body()
                    self._send_json(self.manager_ref.set_ui_state(key, payload.get("value")))
                except ValueError as exc:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if path.startswith("/api/services/"):
                parts = [part for part in path.split("/") if part]
                if len(parts) == 4:
                    _api, _services, slug, action = parts
                    self._handle_service_action(slug, action)
                    return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_service_action(self, slug: str, action: str) -> None:
            try:
                if action == "start":
                    changed = self.manager_ref.start_service(slug)
                elif action == "stop":
                    changed = self.manager_ref.stop_service(slug)
                elif action == "restart":
                    changed = self.manager_ref.restart_service(slug)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unknown action")
                    return
            except KeyError:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown service")
                return
            except (FileNotFoundError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"ok": True, "service": slug, "action": action, "changed": changed})

        def _send_json(self, payload: Any) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._write_body(body)

        def _send_text(self, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._write_body(body)

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                raise ValueError("Request body must be valid JSON") from None
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            return payload

        def _write_body(self, body: bytes) -> None:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def _serve_bounded(self, root: Path, relative_path: str) -> None:
            target = (root / relative_path).resolve()
            root_resolved = root.resolve()
            if target != root_resolved and root_resolved not in target.parents:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            self._serve_file(target)

        def _serve_file(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type_for(path))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._write_body(body)

    return HubHandler


def serve(manager: Any, host: str, port: int, stop_event: threading.Event, ui_dir: Path, common_ui_dir: Path) -> None:
    httpd = ThreadingHTTPServer((host, port), build_handler(manager, stop_event, ui_dir, common_ui_dir))
    httpd.timeout = 0.5
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()