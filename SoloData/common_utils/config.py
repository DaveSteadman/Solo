from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SOLO_DATA_ROOT = Path(__file__).resolve().parents[1]
SOLO_ROOT = SOLO_DATA_ROOT.parent
CONFIG_DIR = SOLO_ROOT / "Config"
FACTORY_DEFAULT_CONFIG = CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = CONFIG_DIR / "local.json"


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


def resolve_data_path(config: dict[str, Any], *parts: str) -> Path:
    """Resolve a path under the configured dataRoot.
    Services should use this instead of per-service path keys.
    Set 'paths.dataRoot' in local.json to an absolute path when data lives
    separately from the code (e.g. 'C:/MyData').
    """
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    base = resolve_solo_path(paths.get("dataRoot"), "./Data")
    return base.joinpath(*parts)


def service_host_port(config: dict[str, Any], slug: str, fallback_port: int) -> tuple[str, int]:
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service = services.get(slug) if isinstance(services.get(slug), dict) else {}
    host = str(service.get("host") or network.get("host") or "127.0.0.1")
    port = int(service.get("port") or fallback_port)
    return host, port


def service_base_url(config: dict[str, Any], slug: str, fallback_port: int) -> str:
    host, port = service_host_port(config, slug, fallback_port)
    return f"http://{host}:{port}"
