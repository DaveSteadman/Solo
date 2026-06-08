# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Ollama-specific SoloLLM client with development conveniences for local model work.

from __future__ import annotations

from typing import Any

from .client import SoloLLMClient
from .http_client import request_json
from .mcp_client import MCPToolClient
from .servers import DEFAULT_OLLAMA_HOST
from .servers import LLMServerConfig
from .servers import ensure_ollama
from .servers import start_ollama
from .tools import ToolRegistry
from .types import ModelInfo


class OllamaClient(SoloLLMClient):
    def __init__(
        self,
        *,
        host: str | None = None,
        model: str = "",
        context_size: int | None = None,
        timeout_seconds: float = 600.0,
        auto_start: bool = True,
        tools: ToolRegistry | None = None,
        mcp: MCPToolClient | None = None,
    ) -> None:
        server = LLMServerConfig.from_backend("ollama", host or DEFAULT_OLLAMA_HOST)
        server.timeout_seconds = timeout_seconds
        server.auto_start = auto_start
        super().__init__(server=server, model=model, context_size=context_size, tools=tools, mcp=mcp)

    def is_running(self) -> bool:
        try:
            self.tags()
            return True
        except Exception:
            return False

    def ensure_running(self) -> None:
        ensure_ollama(self.server)

    def start_server(self) -> None:
        start_ollama()

    def tags(self) -> dict[str, Any]:
        return request_json(f"{self.server.base_url.rstrip('/')}/api/tags", timeout=10.0)

    def list_model_names(self) -> list[str]:
        return [model.id for model in self.list_models()]

    def list_models(self) -> list[ModelInfo]:
        self.ensure_running()
        body = self.tags()
        return [
            ModelInfo(id=str(item.get("model") or item.get("name")), source="ollama", details=item)
            for item in body.get("models", [])
            if item.get("model") or item.get("name")
        ]

    def running_models(self) -> list[dict[str, Any]]:
        try:
            body = request_json(f"{self.server.base_url.rstrip('/')}/api/ps", timeout=10.0)
        except RuntimeError:
            return []
        models = body.get("models")
        return models if isinstance(models, list) else []

    def unload_model(self, model: str | None = None) -> None:
        selected_model = model or self.model
        if not selected_model:
            raise ValueError("A model name is required")
        request_json(
            f"{self.server.base_url.rstrip('/')}/api/generate",
            method="POST",
            payload={"model": selected_model, "prompt": "", "keep_alive": 0, "stream": False},
            timeout=30.0,
        )

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        context_size: int | None = None,
        keep_alive: str | int | None = None,
    ) -> dict[str, Any]:
        selected_model = model or self.model
        if not selected_model:
            raise ValueError("A model name is required")
        self.ensure_running()
        options: dict[str, Any] = {}
        selected_context = context_size if context_size is not None else self.context_size
        if selected_context is not None:
            options["num_ctx"] = selected_context
        payload: dict[str, Any] = {"model": selected_model, "prompt": prompt, "stream": False}
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        return request_json(
            f"{self.server.base_url.rstrip('/')}/api/generate",
            method="POST",
            payload=payload,
            timeout=self.server.timeout_seconds,
        )

    def generate_text(self, prompt: str, **kwargs: Any) -> str:
        return str(self.generate(prompt, **kwargs).get("response") or "")
