# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Launches the SoloAgent service and serves its JSON/common-control web UI.

from __future__ import annotations

import argparse
import json
import signal
import sys
import urllib.parse
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from typing import ClassVar


SOLO_AGENT_ROOT = Path(__file__).resolve().parent
SOLO_ROOT = SOLO_AGENT_ROOT.parent
SOLO_DATA_ROOT = SOLO_ROOT / "SoloData"
APP_DIR = SOLO_AGENT_ROOT / "app"
for path in (SOLO_DATA_ROOT, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common_utils.web import send_json  # noqa: E402
from common_utils.web import serve_bounded_file  # noqa: E402
from common_utils.web import serve_file  # noqa: E402
from solo_engine import SoloAgentEngine  # noqa: E402


CONFIG_DIR = SOLO_ROOT / "Config"
FACTORY_DEFAULT_CONFIG = CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = CONFIG_DIR / "local.json"
UI_DIR = SOLO_AGENT_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SoloAgent service.")
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument("--host", default=None, help="Bind host.")
    parser.add_argument("--port", type=int, default=None, help="Bind port.")
    parser.add_argument("--dry-run", action="store_true", help="Show configuration without starting the server.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    return merge_dict(load_json(FACTORY_DEFAULT_CONFIG), load_json(LOCAL_CONFIG))


def resolve_solo_path(raw: object, default: str) -> Path:
    path = Path(str(raw or default))
    if path.is_absolute():
        return path.resolve()
    return (SOLO_ROOT / path).resolve()


def read_body_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise ValueError("Request body must be valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


class SoloAgentHandler(BaseHTTPRequestHandler):
    engine: ClassVar[SoloAgentEngine]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/ui"):
            serve_file(self, UI_DIR / "index.html")
            return
        if path == "/ui/page.json":
            serve_file(self, UI_DIR / "page.json")
            return
        if path.startswith("/ui/"):
            serve_bounded_file(self, UI_DIR, path.removeprefix("/ui/"))
            return
        if path.startswith("/common/"):
            serve_bounded_file(self, COMMON_UI_DIR, path.removeprefix("/common/"))
            return
        if path == "/status":
            send_json(self, {"status": "ok", "service": "SoloAgent"})
            return
        if path == "/api/snapshot":
            send_json(self, self.engine.snapshot())
            return
        if path in ("/completions", "/api/completions"):
            send_json(self, self.engine.completions())
            return
        if path == "/api/tools":
            send_json(self, {"tools": self.engine.snapshot()["tools"], "skills": self.engine.snapshot()["skills"]})
            return
        if path == "/api/models":
            backend = (params.get("backend") or ["ollama"])[0]
            host = (params.get("host") or [""])[0]
            try:
                send_json(self, {"models": self.engine.list_models(backend=backend, host=host)})
            except Exception as exc:
                send_json(self, {"models": [], "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/run":
            try:
                run = self.engine.run(read_body_json(self))
            except ValueError as exc:
                send_json(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            send_json(self, {"run": asdict(run)})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        print(f"[SoloAgent] {self.address_string()} {format % args}", flush=True)


def build_server(args: argparse.Namespace) -> ThreadingHTTPServer:
    config = load_config()
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service = services.get("soloagent") if isinstance(services.get("soloagent"), dict) else {}
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    host = args.host or str(network.get("host") or "127.0.0.1")
    port = args.port or int(service.get("port") or 9710)
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    data_root = resolve_solo_path(paths.get("dataRoot"), "./Data") / "SoloAgent"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "logs").mkdir(parents=True, exist_ok=True)
    SoloAgentHandler.engine = SoloAgentEngine(config=config, data_root=data_root)
    return ThreadingHTTPServer((host, port), SoloAgentHandler)


def main() -> int:
    args = parse_args()
    config = load_config()
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service = services.get("soloagent") if isinstance(services.get("soloagent"), dict) else {}
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    host = args.host or str(network.get("host") or "127.0.0.1")
    port = args.port or int(service.get("port") or 9710)
    if args.command == "status" or args.dry_run:
        print(json.dumps({"service": "SoloAgent", "host": host, "port": port}, indent=2))
        return 0

    server = build_server(args)
    SoloAgentHandler.engine.start_queue_worker()

    def stop(_signum: int, _frame: object) -> None:
        SoloAgentHandler.engine.stop_queue_worker()
        server.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"[SoloAgent] Serving http://{host}:{port}/ui", flush=True)
    try:
        server.serve_forever()
    finally:
        SoloAgentHandler.engine.stop_queue_worker()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
