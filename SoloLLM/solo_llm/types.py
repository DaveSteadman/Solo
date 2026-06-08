# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Shared data structures used by the SoloLLM client, tool registry and command-line tester.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    id: str
    source: str
    details: dict[str, Any]


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    source: str
    schema: dict[str, Any]
    available: bool = True


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    text: str
    message: dict[str, Any]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    tool_calls: list[LLMToolCall]
    tool_results: list[dict[str, Any]]
    rounds: int
    tokens_per_second: float
