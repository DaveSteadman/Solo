# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# SoloAgent workspace path helpers used by copied KoreAgent skills.

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path


SOLO_AGENT_APP = Path(__file__).resolve().parents[1]
SOLO_AGENT_ROOT = SOLO_AGENT_APP.parent
SOLO_ROOT = SOLO_AGENT_ROOT.parent
CONFIG_DIR = SOLO_ROOT / "Config"


def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge_dict(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


@lru_cache(maxsize=1)
def load_runtime_config() -> dict:
    factory = _read_json_file(CONFIG_DIR / "factory-default.json")
    local = _read_json_file(CONFIG_DIR / "local.json")
    return _merge_dict(factory, local)


def _resolve_solo_path(value: object, default: str) -> Path:
    raw = Path(str(value or default))
    if raw.is_absolute():
        return raw.resolve()
    return (SOLO_ROOT / raw).resolve()


@lru_cache(maxsize=1)
def get_workspace_root() -> Path:
    return SOLO_ROOT


@lru_cache(maxsize=1)
def get_suite_root() -> Path:
    return SOLO_ROOT


@lru_cache(maxsize=1)
def get_suite_config_dir() -> Path:
    return CONFIG_DIR


@lru_cache(maxsize=1)
def get_suite_defaults_file() -> Path:
    return CONFIG_DIR / "factory-default.json"


@lru_cache(maxsize=1)
def get_suite_local_file() -> Path:
    return CONFIG_DIR / "local.json"


@lru_cache(maxsize=1)
def get_bootstrap_defaults_file() -> Path:
    return CONFIG_DIR / "factory-default.json"


@lru_cache(maxsize=1)
def get_controldata_dir() -> Path:
    paths = load_runtime_config().get("paths") or {}
    root = _resolve_solo_path(paths.get("dataRoot"), "./Data")
    path = root / "SoloAgent"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_user_data_dir() -> Path:
    path = get_controldata_dir() / "user"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_logs_dir() -> Path:
    path = get_controldata_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_schedules_dir() -> Path:
    path = get_controldata_dir() / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_test_prompts_dir() -> Path:
    path = get_controldata_dir() / "test_prompts"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_test_results_dir() -> Path:
    path = get_controldata_dir() / "test_results"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_chatsessions_dir() -> Path:
    path = get_controldata_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_chatsessions_named_dir() -> Path:
    path = get_chatsessions_dir() / "named"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_chatsessions_day_dir() -> Path:
    path = get_chatsessions_dir() / datetime.now().strftime("%Y-%m-%d")
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_module_path(module_path: str) -> str:
    text = str(module_path or "").strip().replace("\\", "/")
    if text.startswith("SoloAgent/"):
        return text
    if text.startswith("app/"):
        return f"SoloAgent/{text}"
    return text


def trunc(s: str, n: int) -> str:
    text = str(s)
    if len(text) <= n:
        return text
    return text[: max(0, n - 15)] + "...[truncated]"
