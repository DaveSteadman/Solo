# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# SoloLLM compatibility shim for copied skills that perform an inner LLM extraction.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sys
from pathlib import Path


SOLO_ROOT = Path(__file__).resolve().parents[2]
SOLO_LLM_ROOT = SOLO_ROOT / "SoloLLM"
if str(SOLO_LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_LLM_ROOT))

from solo_llm import LLMServerConfig  # noqa: E402
from solo_llm import SoloLLMClient  # noqa: E402


_ACTIVE_MODEL = ""
_ACTIVE_CONTEXT_SIZE: int | None = None
_ACTIVE_BACKEND = "ollama"
_ACTIVE_HOST = ""


@dataclass
class LLMChatShimResult:
    response: str
    raw: Any = None


def set_active_llm(*, model: str, context_size: int | None, backend: str = "ollama", host: str = "") -> None:
    global _ACTIVE_MODEL, _ACTIVE_CONTEXT_SIZE, _ACTIVE_BACKEND, _ACTIVE_HOST
    _ACTIVE_MODEL = str(model or "")
    _ACTIVE_CONTEXT_SIZE = context_size
    _ACTIVE_BACKEND = str(backend or "ollama")
    _ACTIVE_HOST = str(host or "")


def get_active_model() -> str:
    return _ACTIVE_MODEL


def get_active_num_ctx() -> int | None:
    return _ACTIVE_CONTEXT_SIZE


def call_llm_chat(
    *,
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    num_ctx: int | None = None,
) -> LLMChatShimResult:
    del tools
    server = LLMServerConfig.from_backend(_ACTIVE_BACKEND, _ACTIVE_HOST or None)
    client = SoloLLMClient(server=server, model=model_name, context_size=num_ctx or _ACTIVE_CONTEXT_SIZE)
    user_text = ""
    prior: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "user":
            user_text = str(message.get("content") or "")
        else:
            prior.append(message)
    result = client.chat(user_text, messages=prior, use_tools=False)
    return LLMChatShimResult(response=result.text, raw=result)


def call_ollama(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("Direct Ollama calls are not available in SoloAgent. Use SoloLLMClient.")


def ensure_ollama_running() -> None:
    LLMServerConfig.from_backend("ollama")


def list_ollama_models() -> list[str]:
    client = SoloLLMClient(server=LLMServerConfig.from_backend("ollama"))
    return [model.id for model in client.list_models()]


def resolve_model_name(name: str) -> str:
    return str(name or "").strip()


def format_running_model_report() -> str:
    return f"SoloLLM backend={_ACTIVE_BACKEND} model={_ACTIVE_MODEL or '(none)'}"
