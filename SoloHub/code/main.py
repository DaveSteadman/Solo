from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, IO


CODE_DIR = Path(__file__).resolve().parent
HUB_ROOT = CODE_DIR.parent
SOLO_ROOT = HUB_ROOT.parent
SOLO_CONFIG_DIR = SOLO_ROOT / "Config"
DEFAULT_CONFIG = SOLO_CONFIG_DIR / "hub.json"
FACTORY_DEFAULT_CONFIG = SOLO_CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = SOLO_CONFIG_DIR / "local.json"
UI_DIR = HUB_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"


@dataclass(frozen=True)
class ServiceSpec:
    slug: str
    label: str
    description: str
    cwd: Path
    command: tuple[str, ...]
    url: str
    health_url: str
    enabled: bool
    auto_start: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SoloHub child-process manager.")
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to the hub JSON config.")
    parser.add_argument("--host", default=None, help="Hub bind host.")
    parser.add_argument("--port", type=int, default=None, help="Hub bind port.")
    parser.add_argument("--open-browser", action="store_true", help="Open SoloHub in the default browser.")
    parser.add_argument("--start-auto", action="store_true", default=True, help="Start services marked autoStart when the hub starts.")
    parser.add_argument("--no-start-auto", action="store_false", dest="start_auto", help="Do not start autoStart services when the hub starts.")
    parser.add_argument("--dry-run", action="store_true", help="Show configured child processes without starting the hub.")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"Config must be a JSON object: {path}")
    return data


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_local_config() -> dict[str, Any]:
    config: dict[str, Any] = {}
    for path in (FACTORY_DEFAULT_CONFIG, LOCAL_CONFIG):
        if not path.exists():
            continue
        config = merge_dict(config, load_config(path))
    return config


def load_hub_config(path: Path) -> dict[str, Any]:
    config = load_config(path)
    local_hub_config = SOLO_CONFIG_DIR / "hub.local.json"
    if local_hub_config.exists():
        config = merge_dict(config, load_config(local_hub_config))
    return config


def resolve_path(raw: str | None, base: Path) -> Path:
    if not raw:
        return base
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def expand_command(command: list[Any]) -> tuple[str, ...]:
    if not command:
        raise SystemExit("Each service needs a command array.")
    values: list[str] = []
    for item in command:
        text = str(item)
        values.append(text.replace("{python}", sys.executable))
    return tuple(values)


def load_services(config: dict[str, Any], local_config: dict[str, Any]) -> list[ServiceSpec]:
    services: list[ServiceSpec] = []
    raw_services = config.get("services", [])
    if not isinstance(raw_services, list):
        raise SystemExit("Config field services must be an array.")
    network = local_config.get("network") if isinstance(local_config.get("network"), dict) else {}
    service_ports = local_config.get("services") if isinstance(local_config.get("services"), dict) else {}
    default_host = str(network.get("host") or "127.0.0.1")

    for raw in raw_services:
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("slug", "")).strip().lower()
        if not slug:
            raise SystemExit("Each service needs a slug.")
        service_config = service_ports.get(slug) if isinstance(service_ports.get(slug), dict) else {}
        url, health_url = service_urls(raw, service_config, default_host)
        services.append(
            ServiceSpec(
                slug=slug,
                label=str(raw.get("label") or slug),
                description=str(raw.get("description") or ""),
                cwd=resolve_path(str(raw.get("cwd") or "."), SOLO_CONFIG_DIR),
                command=expand_command(raw.get("command") if isinstance(raw.get("command"), list) else []),
                url=url,
                health_url=health_url,
                enabled=bool(raw.get("enabled", True)),
                auto_start=bool(raw.get("autoStart", False)),
            )
        )
    return services


def service_urls(raw: dict[str, Any], service_config: dict[str, Any], default_host: str) -> tuple[str, str]:
    port = service_config.get("port")
    host = str(service_config.get("host") or default_host)
    raw_url = str(raw.get("url") or "")
    raw_health_url = str(raw.get("healthUrl") or raw_url)

    if port is None:
        return raw_url, raw_health_url

    url_path = str(raw.get("urlPath") or path_from_url(raw_url) or "/")
    health_path = str(raw.get("healthPath") or path_from_url(raw_health_url) or url_path)
    base_url = f"http://{host}:{int(port)}"
    return base_url + normalize_url_path(url_path), base_url + normalize_url_path(health_path)


def path_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def normalize_url_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def resolve_solo_path(raw: object, default: str) -> Path:
    path = Path(str(raw or default))
    if path.is_absolute():
        return path.resolve()
    return (SOLO_ROOT / path).resolve()


def probe_http(url: str, timeout: float = 1.0) -> tuple[bool, str]:
    if not url:
        return False, "no health url"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 500:
            return True, f"HTTP {exc.code}"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, exc.__class__.__name__


def find_listening_pid(url: str) -> int | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if not port or host not in ("127.0.0.1", "localhost", "::1"):
        return None
    if os.name != "nt":
        return None
    try:
        output = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, encoding="utf-8", errors="replace")
    except Exception:
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING" or not parts[1].endswith(f":{port}"):
            continue
        try:
            return int(parts[4])
        except ValueError:
            return None
    return None


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            output = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True, encoding="utf-8", errors="replace")
            return str(pid) in output
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: int) -> bool:
    if pid <= 0 or not is_pid_running(pid):
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return not is_pid_running(pid)
    try:
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 8
        while time.time() < deadline:
            if not is_pid_running(pid):
                return True
            time.sleep(0.2)
        os.kill(pid, signal.SIGKILL)
        return not is_pid_running(pid)
    except OSError:
        return False


class HubManager:
    def __init__(self, services: list[ServiceSpec], log_dir: Path, data_root: Path) -> None:
        self._services = services
        self._service_map = {service.slug: service for service in services}
        self._log_dir = log_dir
        self._data_root = data_root
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._external_pids: dict[str, int] = {}
        self._started_at: dict[str, float] = {}
        self._log_handles: dict[str, IO[bytes]] = {}
        self._lock = threading.Lock()

    def start_auto(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for service in self._services:
            if service.enabled and service.auto_start:
                try:
                    result[service.slug] = self.start_service(service.slug)
                except Exception as exc:  # noqa: BLE001
                    result[service.slug] = False
                    print(f"[SoloHub] Could not start {service.slug}: {exc}", flush=True)
        return result

    def start_service(self, slug: str) -> bool:
        service = self._get_service(slug)
        if not service.enabled:
            raise ValueError(f"{service.label} is disabled")

        with self._lock:
            existing = self._processes.get(slug)
            if existing is not None and existing.poll() is None:
                return False

        if not service.cwd.exists():
            raise FileNotFoundError(f"Missing cwd for {service.label}: {service.cwd}")

        reachable, _detail = probe_http(service.health_url)
        if reachable:
            pid = find_listening_pid(service.health_url)
            if pid:
                with self._lock:
                    self._external_pids[slug] = pid
            return False

        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_handle: IO[bytes] = open(self._log_dir / f"{slug}.log", "ab")  # noqa: SIM115
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["SOLO_ROOT"] = str(SOLO_ROOT)
        env["SOLO_HUB_ROOT"] = str(HUB_ROOT)

        process = subprocess.Popen(
            list(service.command),
            cwd=str(service.cwd),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        with self._lock:
            old_handle = self._log_handles.pop(slug, None)
            self._processes[slug] = process
            self._started_at[slug] = time.time()
            self._log_handles[slug] = log_handle
        if old_handle is not None:
            old_handle.close()
        return True

    def stop_service(self, slug: str) -> bool:
        service = self._get_service(slug)
        with self._lock:
            process = self._processes.get(slug)
            external_pid = self._external_pids.pop(slug, None)
        if process is None or process.poll() is not None:
            if external_pid is not None:
                return terminate_pid(external_pid)
            listener_pid = find_listening_pid(service.health_url)
            return terminate_pid(listener_pid) if listener_pid is not None else False

        changed = False
        process.terminate()
        try:
            process.wait(timeout=8)
            changed = True
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            changed = True

        listener_pid = find_listening_pid(service.health_url)
        if listener_pid is not None and listener_pid != process.pid:
            changed = terminate_pid(listener_pid) or changed

        with self._lock:
            handle = self._log_handles.pop(slug, None)
            self._external_pids.pop(slug, None)
        if handle is not None:
            handle.close()
        return changed

    def restart_service(self, slug: str) -> bool:
        self.stop_service(slug)
        return self.start_service(slug)

    def stop_all(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for service in reversed(self._services):
            try:
                result[service.slug] = self.stop_service(service.slug)
            except Exception as exc:  # noqa: BLE001
                result[service.slug] = False
                print(f"[SoloHub] Could not stop {service.slug}: {exc}", flush=True)
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            processes = dict(self._processes)
            external_pids = dict(self._external_pids)
            started_at = dict(self._started_at)

        cwd_exists_by_slug = {service.slug: service.cwd.exists() for service in self._services}
        probe_results: dict[str, tuple[bool, str]] = {}

        def probe_service(service: ServiceSpec) -> tuple[str, bool, str]:
            cwd_exists = cwd_exists_by_slug[service.slug]
            process = processes.get(service.slug)
            running = process is not None and process.poll() is None
            if not cwd_exists and not running:
                return service.slug, False, "missing cwd"
            reachable, detail = probe_http(service.health_url, timeout=0.35)
            return service.slug, reachable, detail

        with ThreadPoolExecutor(max_workers=max(1, len(self._services))) as pool:
            for slug, reachable, detail in pool.map(probe_service, self._services):
                probe_results[slug] = (reachable, detail)

        service_entries = []
        for service in self._services:
            process = processes.get(service.slug)
            external_pid = external_pids.get(service.slug)
            if external_pid is not None and not is_pid_running(external_pid):
                external_pid = None
            process_running = process is not None and process.poll() is None
            running = process_running or external_pid is not None
            reachable, probe = probe_results.get(service.slug, (False, "not probed"))
            cwd_exists = cwd_exists_by_slug[service.slug]
            state, state_label = self._state(service, process, running, reachable, cwd_exists)
            service_entries.append(
                {
                    "slug": service.slug,
                    "label": service.label,
                    "description": service.description,
                    "cwd": str(service.cwd),
                    "command": list(service.command),
                    "url": service.url,
                    "healthUrl": service.health_url,
                    "enabled": service.enabled,
                    "autoStart": service.auto_start,
                    "startable": service.enabled and cwd_exists,
                    "cwdExists": cwd_exists,
                    "running": running,
                    "reachable": reachable,
                    "probe": probe,
                    "pid": process.pid if process_running else external_pid,
                    "returncode": None if process is None or process_running else process.returncode,
                    "uptimeSec": round(time.time() - started_at[service.slug], 1) if running and service.slug in started_at else None,
                    "state": state,
                    "stateLabel": state_label,
                }
            )

        return {
            "hub": {
                "label": "SoloHub",
                "root": str(SOLO_ROOT),
                "paths": {
                    "soloRoot": str(SOLO_ROOT),
                    "dataRoot": str(self._data_root),
                },
                "metrics": {
                    "configured": len(service_entries),
                    "running": sum(1 for item in service_entries if item["running"]),
                    "reachable": sum(1 for item in service_entries if item["reachable"]),
                    "missing": sum(1 for item in service_entries if item["enabled"] and not item["cwdExists"]),
                },
            },
            "services": service_entries,
        }

    def close(self) -> None:
        self.stop_all()
        with self._lock:
            handles = list(self._log_handles.values())
            self._log_handles.clear()
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass

    def _get_service(self, slug: str) -> ServiceSpec:
        service = self._service_map.get(slug)
        if service is None:
            raise KeyError(slug)
        return service

    @staticmethod
    def _state(
        service: ServiceSpec,
        process: subprocess.Popen[bytes] | None,
        running: bool,
        reachable: bool,
        cwd_exists: bool,
    ) -> tuple[str, str]:
        if not service.enabled:
            return "stopped", "Disabled"
        if not cwd_exists:
            return "missing", "Missing"
        if running and reachable:
            return "running", "Running"
        if running:
            return "running", "Starting"
        if reachable:
            return "external", "External"
        if process is not None and process.returncode is not None:
            return "exited", f"Exited {process.returncode}"
        return "stopped", "Stopped"


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


def build_handler(manager: HubManager, stop_event: threading.Event):
    class HubHandler(BaseHTTPRequestHandler):
        manager_ref: ClassVar[HubManager] = manager
        stop_ref: ClassVar[threading.Event] = stop_event

        def do_GET(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path in ("", "/", "/ui"):
                self._serve_file(UI_DIR / "index.html")
                return
            if path == "/status":
                self._send_json({"status": "ok", "service": "SoloHub"})
                return
            if path == "/api/snapshot":
                self._send_json(self.manager_ref.snapshot())
                return
            if path.startswith("/ui/"):
                self._serve_bounded(UI_DIR, path.removeprefix("/ui/"))
                return
            if path.startswith("/common/"):
                self._serve_bounded(COMMON_UI_DIR, path.removeprefix("/common/"))
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
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
            self.wfile.write(body)

    return HubHandler


def serve(manager: HubManager, host: str, port: int, stop_event: threading.Event) -> None:
    httpd = ThreadingHTTPServer((host, port), build_handler(manager, stop_event))
    httpd.timeout = 0.5
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()


def print_snapshot(snapshot: dict[str, Any]) -> None:
    print("SoloHub status")
    for service in snapshot["services"]:
        pid = service["pid"] if service["pid"] is not None else "-"
        print(f"  {service['label']:<10} {service['stateLabel']:<12} pid={pid:<8} cwd={service['cwd']}")


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_hub_config(config_path)
    local_config = load_local_config()
    hub_config = config.get("hub") if isinstance(config.get("hub"), dict) else {}
    network = local_config.get("network") if isinstance(local_config.get("network"), dict) else {}
    local_services = local_config.get("services") if isinstance(local_config.get("services"), dict) else {}
    local_hub = local_services.get("solohub") if isinstance(local_services.get("solohub"), dict) else {}
    host = args.host or str(local_hub.get("host") or network.get("host") or hub_config.get("host") or "127.0.0.1")
    port = int(args.port or local_hub.get("port") or hub_config.get("port") or 9700)
    paths_config = local_config.get("paths") if isinstance(local_config.get("paths"), dict) else {}
    data_root = resolve_solo_path(paths_config.get("dataRoot"), "./Data")
    log_dir = data_root / "SoloHub" / "logs"
    manager = HubManager(load_services(config, local_config), log_dir, data_root)

    if args.command == "status":
        print_snapshot(manager.snapshot())
        return 0

    if args.dry_run:
        print_snapshot(manager.snapshot())
        print(f"  hub        http://{host}:{port}/")
        return 0

    stop_event = threading.Event()
    if args.start_auto:
        manager.start_auto()

    def handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    print(f"SoloHub: http://{host}:{port}/", flush=True)
    if args.open_browser:
        webbrowser.open(f"http://{host}:{port}/")

    try:
        serve(manager, host, port, stop_event)
    finally:
        manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
