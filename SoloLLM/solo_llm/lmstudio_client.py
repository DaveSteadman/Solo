# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# LM Studio-specific SoloLLM client for models served through LM Studio's OpenAI-compatible API.

from __future__ import annotations

from .client import SoloLLMClient
from .http_client import request_json
from .mcp_client import MCPToolClient
from .servers import DEFAULT_LMSTUDIO_HOST
from .servers import LLMServerConfig
from .tools import ToolRegistry
from .types import ModelInfo


class LMStudioClient(SoloLLMClient):
    def __init__(
        self,
        *,
        host: str | None = None,
        model: str = "",
        context_size: int | None = None,
        timeout_seconds: float = 600.0,
        tools: ToolRegistry | None = None,
        mcp: MCPToolClient | None = None,
    ) -> None:
        server = LLMServerConfig.from_backend("lmstudio", host or DEFAULT_LMSTUDIO_HOST)
        server.timeout_seconds = timeout_seconds
        server.auto_start = False
        super().__init__(server=server, model=model, context_size=context_size, tools=tools, mcp=mcp)

    def is_running(self) -> bool:
        try:
            self.models_response()
            return True
        except Exception:
            return False

    def ensure_running(self) -> None:
        request_json(self.server.models_url, timeout=5.0)

    def models_response(self) -> dict:
        return request_json(self.server.models_url, timeout=10.0)

    def list_model_names(self) -> list[str]:
        return [model.id for model in self.list_models()]

    def list_models(self) -> list[ModelInfo]:
        body = self.models_response()
        return [
            ModelInfo(id=str(item.get("id")), source="lmstudio", details=item)
            for item in body.get("data", [])
            if item.get("id")
        ]

    def runtime_report(self, model: str | None = None) -> str:
        selected_model = model or self.model or "(no model selected)"
        return f"Model runtime status: {selected_model} via LM Studio at {self.server.base_url}"
