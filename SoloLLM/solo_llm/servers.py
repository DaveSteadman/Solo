# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Server configuration and discovery helpers for Ollama, LM Studio and OpenAI-compatible hosts.

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any

from .http_client import request_json
from .types import ModelInfo


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_LMSTUDIO_HOST = "http://localhost:1234"


@dataclass
class LLMServerConfig:
    backend: str = "ollama"
    base_url: str = DEFAULT_OLLAMA_HOST
    api_key: str | None = None
    timeout_seconds: float = 600.0
    auto_start: bool = True

    @classmethod
    def from_backend(cls, backend: str, host: str | None = None, api_key: str | None = None) -> "LLMServerConfig":
        backend = backend.strip().lower()
        if backend == "lmstudio":
            return cls(backend="lmstudio", base_url=_normalize_host(host, DEFAULT_LMSTUDIO_HOST, 1234), api_key=api_key)
        if backend == "openai":
            return cls(backend="openai", base_url=(host or "https://api.openai.com").rstrip("/"), api_key=api_key, auto_start=False)
        if backend == "ollama":
            return cls(backend="ollama", base_url=_normalize_host(host, DEFAULT_OLLAMA_HOST, 11434), api_key=api_key)
        raise ValueError("backend must be ollama, lmstudio or openai")

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/models"


def _normalize_host(host: str | None, default_url: str, default_port: int) -> str:
    if not host:
        return default_url
    text = host.strip()
    if "://" in text:
        return text.rstrip("/")
    if ":" not in text:
        return f"http://{text}:{default_port}"
    return f"http://{text}"


def ensure_server(config: LLMServerConfig) -> None:
    if config.backend == "ollama":
        ensure_ollama(config)
        return
    request_json(config.models_url, headers=config.headers, timeout=5.0)


def ensure_ollama(config: LLMServerConfig) -> None:
    if _ollama_tags(config):
        return
    if not config.auto_start or not _is_local(config.base_url):
        raise RuntimeError(f"Ollama is not reachable at {config.base_url}")
    start_ollama()
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if _ollama_tags(config):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Ollama did not become ready at {config.base_url}")


def start_ollama() -> None:
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    except FileNotFoundError:
        raise RuntimeError("'ollama' executable was not found on PATH") from None


def list_models(config: LLMServerConfig) -> list[ModelInfo]:
    if config.backend == "ollama":
        body = request_json(f"{config.base_url.rstrip('/')}/api/tags", headers=config.headers, timeout=10.0)
        return [
            ModelInfo(id=str(item.get("model") or item.get("name")), source="ollama", details=item)
            for item in body.get("models", [])
            if item.get("model") or item.get("name")
        ]
    body = request_json(config.models_url, headers=config.headers, timeout=10.0)
    return [
        ModelInfo(id=str(item.get("id")), source=config.backend, details=item)
        for item in body.get("data", [])
        if item.get("id")
    ]


def _ollama_tags(config: LLMServerConfig) -> bool:
    try:
        request_json(f"{config.base_url.rstrip('/')}/api/tags", headers=config.headers, timeout=3.0)
        return True
    except Exception:
        return False


def _is_local(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url
