# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Launches the SoloCode local coding-agent service and serves its JSON-specified UI.

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from typing import ClassVar


SOLO_CODE_ROOT = Path(__file__).resolve().parent
SOLO_ROOT = SOLO_CODE_ROOT.parent
SOLO_DATA_ROOT = SOLO_ROOT / "SoloData"
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.web import send_json  # noqa: E402
from common_utils.web import serve_bounded_file  # noqa: E402
from common_utils.web import serve_file  # noqa: E402


CONFIG_DIR = SOLO_ROOT / "Config"
FACTORY_DEFAULT_CONFIG = CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = CONFIG_DIR / "local.json"
UI_DIR = SOLO_CODE_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()
MAX_READ_BYTES = 1_500_000
MAX_TREE_ITEMS = 1600
MAX_TREE_DEPTH = 8
IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".pyi",
    ".sql",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass
class FileRecord:
    path: str
    name: str
    content: str
    encoding: str
    size: int
    modified_at: int
    modified_at_ns: int
    content_hash: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SoloCode local coding-agent service.")
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


def service_base_url(config: dict[str, Any], slug: str, fallback_port: int) -> str:
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service = services.get(slug) if isinstance(services.get(slug), dict) else {}
    host = str(service.get("host") or network.get("host") or "127.0.0.1")
    port = int(service.get("port") or fallback_port)
    return f"http://{host}:{port}"


def parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return False
    return b"\x00" not in sample


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SoloCodeWorkspace:
    def __init__(self, default_root: Path, data_root: Path, chat_base_url: str) -> None:
        self.default_root = default_root.resolve()
        self.active_root = self.default_root
        self.data_root = data_root
        self.service_data_root = data_root / "SoloCode"
        self.log_dir = self.service_data_root / "logs"
        self.chat_base_url = chat_base_url.rstrip("/")
        self._lock = threading.Lock()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active_root = self.active_root
        return {
            "service": {
                "label": "SoloCode",
                "status": "ok",
                "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
            },
            "paths": {
                "soloRoot": str(SOLO_ROOT),
                "dataRoot": str(self.data_root),
                "serviceDataRoot": str(self.service_data_root),
                "workspaceRoot": str(active_root),
                "logDir": str(self.log_dir),
            },
            "workspace": {
                "root": str(active_root),
                "rootLabel": self.root_label(active_root),
                "options": self.root_options(),
            },
            "chat": {
                "baseUrl": self.chat_base_url,
                "externalId": self.chat_external_id(active_root),
            },
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            active_root = self.active_root
        return {
            "service": "SoloCode",
            "status": "ok",
            "workspaceRoot": str(active_root),
            "dataRoot": str(self.service_data_root),
        }

    def root_options(self) -> list[dict[str, str]]:
        roots = [SOLO_ROOT]
        try:
            roots.extend(path.resolve() for path in SOLO_ROOT.iterdir() if path.is_dir() and path.name not in IGNORED_DIRS)
        except OSError:
            pass
        with self._lock:
            active_root = self.active_root
        if active_root not in roots:
            roots.append(active_root)
        return [{"label": self.root_label(path), "path": str(path)} for path in roots]

    def set_root(self, raw_root: str) -> dict[str, Any]:
        candidate = self.normalize_root(raw_root)
        if not candidate.exists():
            raise ValueError("Root folder not found")
        if not candidate.is_dir():
            raise ValueError("Root must be a directory")
        with self._lock:
            self.active_root = candidate
        return self.snapshot()

    def tree(self, path: str = "") -> dict[str, Any]:
        root = self.resolve_path(path)
        if not root.exists() or not root.is_dir():
            raise ValueError("Folder not found")
        counter = {"count": 0, "truncated": False}
        return {
            "root": str(self.current_root()),
            "path": self.relative_path(root),
            "items": self.tree_children(root, 0, counter),
            "truncated": counter["truncated"],
        }

    def read_file(self, rel_path: str) -> FileRecord:
        path = self.resolve_path(rel_path)
        if not path.exists():
            raise FileNotFoundError("File not found")
        if not path.is_file():
            raise ValueError("Path is not a file")
        if not is_text_file(path):
            raise ValueError("Binary files are not supported")
        raw = path.read_bytes()
        if len(raw) > MAX_READ_BYTES:
            raise ValueError("File too large for editor view")
        content, encoding = decode_text(raw)
        stat = path.stat()
        return FileRecord(
            path=self.relative_path(path),
            name=path.name,
            content=content,
            encoding=encoding,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
            modified_at_ns=int(stat.st_mtime_ns),
            content_hash=content_hash(content),
        )

    def write_file(self, rel_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_path(rel_path)
        if not path.exists():
            raise FileNotFoundError("File not found")
        if not path.is_file():
            raise ValueError("Path is not a file")
        existing = self.read_file(rel_path)
        expected_hash = str(payload.get("expected_hash") or "").strip()
        if expected_hash and expected_hash != existing.content_hash:
            raise RuntimeError("File changed on disk")
        content = str(payload.get("content") or "")
        path.write_text(content, encoding="utf-8", newline="")
        return self.read_file(rel_path).__dict__ | {"ok": True}

    def context_pack(self, rel_path: str, start_line: int | None, end_line: int | None) -> dict[str, Any]:
        record = self.read_file(rel_path)
        lines = record.content.splitlines()
        total_lines = max(1, len(lines))
        start = max(1, min(start_line or 1, total_lines))
        end = max(start, min(end_line or start, total_lines))
        symbol = python_symbol_context(record.content, start, end) if record.path.endswith((".py", ".pyi")) else None
        return {
            "path": record.path,
            "selection": {
                "start_line": start,
                "end_line": end,
                "content": "\n".join(lines[start - 1:end]),
            },
            "nearby": line_window(lines, start, end, 30),
            "symbol": symbol,
            "total_lines": total_lines,
        }

    def send_chat_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            active_root = self.active_root
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Prompt cannot be empty")
        file_path = str(payload.get("path") or "").strip()
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        content = build_prompt(prompt, file_path, selection, context)
        external_id = self.chat_external_id(active_root)
        url = f"{self.chat_base_url}/api/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}/messages"
        result = http_json(
            url,
            method="POST",
            payload={
                "channel_type": "service",
                "profile": "admin",
                "subject": "SoloCode",
                "background_context": "SoloCode coding-agent conversation. Use repository context in each prompt.",
                "direction": "inbound",
                "content": content,
                "sender_display": "SoloCode",
                "status": "received",
                "queue_response": True,
                "protected": True,
            },
        )
        return result | {"external_id": external_id}

    def chat_detail(self) -> dict[str, Any]:
        with self._lock:
            active_root = self.active_root
        external_id = self.chat_external_id(active_root)
        url = f"{self.chat_base_url}/api/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}/detail"
        try:
            detail = http_json(url, timeout=2.0)
        except OSError:
            return {"external_id": external_id, "messages": [], "conversation": None}
        return detail | {"external_id": external_id}

    def current_root(self) -> Path:
        with self._lock:
            return self.active_root

    def normalize_root(self, raw_root: str) -> Path:
        raw = str(raw_root or "").strip()
        if not raw:
            return SOLO_ROOT.resolve()
        path = Path(raw).expanduser()
        if not path.is_absolute():
            return (SOLO_ROOT / path).resolve()
        return path.resolve()

    def resolve_path(self, rel_path: str) -> Path:
        root = self.current_root()
        candidate = (root / str(rel_path or "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Path escapes workspace root") from exc
        return candidate

    def relative_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.current_root()).as_posix()
        except ValueError:
            return ""

    def tree_children(self, folder: Path, depth: int, counter: dict[str, Any]) -> list[dict[str, Any]]:
        if depth >= MAX_TREE_DEPTH or counter["count"] >= MAX_TREE_ITEMS:
            counter["truncated"] = counter["count"] >= MAX_TREE_ITEMS
            return []
        try:
            entries = sorted(folder.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            return []
        children: list[dict[str, Any]] = []
        for entry in entries:
            if counter["count"] >= MAX_TREE_ITEMS:
                counter["truncated"] = True
                break
            if entry.name in IGNORED_DIRS:
                continue
            if entry.is_dir():
                counter["count"] += 1
                children.append({
                    "name": entry.name,
                    "path": self.relative_path(entry),
                    "type": "folder",
                    "children": self.tree_children(entry, depth + 1, counter),
                })
                continue
            if not entry.is_file() or not is_text_file(entry):
                continue
            counter["count"] += 1
            children.append({
                "name": entry.name,
                "path": self.relative_path(entry),
                "type": "file",
            })
        return children

    @staticmethod
    def root_label(path: Path) -> str:
        try:
            rel = path.relative_to(SOLO_ROOT)
            return SOLO_ROOT.name if str(rel) == "." else rel.as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def chat_external_id(root: Path) -> str:
        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:10]
        return f"solocode:{digest}"


def decode_text(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("File is not a supported text encoding")


def line_window(lines: list[str], start: int, end: int, pad: int) -> dict[str, Any]:
    from_line = max(1, start - pad)
    to_line = min(len(lines), end + pad)
    return {
        "from_line": from_line,
        "to_line": to_line,
        "content": "\n".join(lines[from_line - 1:to_line]),
    }


def python_symbol_context(content: str, start: int, end: int) -> dict[str, Any] | None:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    best: dict[str, Any] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        node_start = getattr(node, "lineno", None)
        node_end = getattr(node, "end_lineno", None)
        if node_start is None or node_end is None or node_start > start or node_end < end:
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        candidate = {"name": node.name, "kind": kind, "start_line": node_start, "end_line": node_end}
        if best is None or (candidate["end_line"] - candidate["start_line"]) < (best["end_line"] - best["start_line"]):
            best = candidate
    return best


def build_prompt(prompt: str, file_path: str, selection: dict[str, Any], context: dict[str, Any]) -> str:
    parts = ["SoloCode prompt:", prompt]
    if file_path:
        parts.append(f"\nSelected file: {file_path}")
    selected_text = str(selection.get("text") or "").strip()
    if selected_text:
        start_line = selection.get("startLine") or selection.get("start_line") or "?"
        end_line = selection.get("endLine") or selection.get("end_line") or start_line
        parts.append(f"\nSelected lines {start_line}-{end_line}:\n```text\n{selected_text}\n```")
    nearby = context.get("nearby") if isinstance(context.get("nearby"), dict) else {}
    nearby_content = str(nearby.get("content") or "").strip()
    if nearby_content:
        parts.append(f"\nNearby context:\n```text\n{nearby_content}\n```")
    return "\n".join(parts)


def http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "SoloCode/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise OSError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OSError(f"{url} unreachable: {exc.reason}") from exc
    data = json.loads(body or "{}")
    return data if isinstance(data, dict) else {}


def build_handler(workspace: SoloCodeWorkspace):
    class CodeHandler(BaseHTTPRequestHandler):
        workspace_ref: ClassVar[SoloCodeWorkspace] = workspace

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query)
            try:
                if path in ("", "/", "/ui"):
                    serve_file(self, UI_DIR / "index.html")
                    return
                if path == "/status":
                    send_json(self, self.workspace_ref.status())
                    return
                if path == "/api/snapshot":
                    send_json(self, self.workspace_ref.snapshot())
                    return
                if path == "/api/tree":
                    send_json(self, self.workspace_ref.tree(query_text(params, "path")))
                    return
                if path == "/api/file":
                    send_json(self, self.workspace_ref.read_file(query_text(params, "path")).__dict__)
                    return
                if path == "/api/context":
                    send_json(
                        self,
                        self.workspace_ref.context_pack(
                            query_text(params, "path"),
                            query_optional_int(params, "start_line"),
                            query_optional_int(params, "end_line"),
                        ),
                    )
                    return
                if path == "/api/chat":
                    send_json(self, self.workspace_ref.chat_detail())
                    return
                if path.startswith("/ui/"):
                    serve_bounded_file(self, UI_DIR, path.removeprefix("/ui/"))
                    return
                if path.startswith("/common/"):
                    serve_bounded_file(self, COMMON_UI_DIR, path.removeprefix("/common/"))
                    return
            except FileNotFoundError as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            except (ValueError, RuntimeError, OSError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == "/api/root":
                    send_json(self, self.workspace_ref.set_root(str(self.read_json().get("root") or "")))
                    return
                if parsed.path == "/api/chat":
                    send_json(self, self.workspace_ref.send_chat_prompt(self.read_json()), HTTPStatus.CREATED)
                    return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except OSError as exc:
                self.send_error(HTTPStatus.BAD_GATEWAY, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_PUT(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            try:
                if parsed.path == "/api/file":
                    send_json(self, self.workspace_ref.write_file(query_text(params, "path"), self.read_json()))
                    return
            except FileNotFoundError as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            except RuntimeError as exc:
                self.send_error(HTTPStatus.CONFLICT, str(exc))
                return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def read_json(self) -> dict[str, Any]:
            length = parse_int(self.headers.get("Content-Length"), 0)
            if length <= 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}

    return CodeHandler


def query_text(params: dict[str, list[str]], name: str) -> str:
    return str((params.get(name) or [""])[0]).strip()


def query_optional_int(params: dict[str, list[str]], name: str) -> int | None:
    value = query_text(params, name)
    if not value:
        return None
    return parse_int(value, 0) or None


def serve(workspace: SoloCodeWorkspace, host: str, port: int, stop_event: threading.Event) -> None:
    httpd = ThreadingHTTPServer((host, port), build_handler(workspace))
    httpd.timeout = 0.5
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()


def print_status(workspace: SoloCodeWorkspace, host: str, port: int) -> None:
    snapshot = workspace.snapshot()
    print("SoloCode status")
    print(f"  url        http://{host}:{port}/")
    print(f"  workspace  {snapshot['paths']['workspaceRoot']}")
    print(f"  data       {snapshot['paths']['serviceDataRoot']}")


def main() -> int:
    args = parse_args()
    config = load_config()
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    solocode = services.get("solocode") if isinstance(services.get("solocode"), dict) else {}
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    host = args.host or str(solocode.get("host") or network.get("host") or "127.0.0.1")
    port = int(args.port or solocode.get("port") or 9760)
    data_root = resolve_solo_path(paths.get("dataRoot"), "./Data")
    workspace_root = resolve_solo_path(paths.get("soloCodeWorkspaceRoot"), ".")
    chat_base_url = service_base_url(config, "solochat", 9720)
    workspace = SoloCodeWorkspace(workspace_root, data_root, chat_base_url)

    if args.command == "status" or args.dry_run:
        print_status(workspace, host, port)
        return 0

    stop_event = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    print(f"SoloCode: http://{host}:{port}/", flush=True)
    serve(workspace, host, port, stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
