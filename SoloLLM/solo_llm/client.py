# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# High-level SoloLLM client that hides provider calls, model listing, local tools and MCP tools.

from __future__ import annotations

import json
import time
from typing import Any

from .http_client import request_json
from .mcp_client import MCPToolClient
from .servers import LLMServerConfig
from .servers import ensure_server
from .servers import list_models
from .tools import ToolRegistry
from .tools import tool_result_to_text
from .types import ChatResult
from .types import LLMToolCall
from .types import ModelInfo
from .types import ToolInfo


class SoloLLMClient:
    def __init__(
        self,
        *,
        server: LLMServerConfig | None = None,
        model: str = "",
        context_size: int | None = None,
        tools: ToolRegistry | None = None,
        mcp: MCPToolClient | None = None,
    ) -> None:
        self.server = server or LLMServerConfig()
        self.model = model
        self.context_size = context_size
        self.tools = tools or ToolRegistry()
        self.mcp = mcp
        self._mcp_started = False

    def start(self) -> None:
        ensure_server(self.server)
        self._start_mcp()

    def _start_mcp(self) -> None:
        if self.mcp is not None and not self._mcp_started:
            self.mcp.start()
            for definition in self.mcp.tool_definitions():
                source = "mcp"
                name = definition.get("function", {}).get("name")
                if name:
                    source = f"mcp:{name}"
                self.tools.merge_tool_definition(definition, source=source)
            self._mcp_started = True

    def stop(self) -> None:
        if self.mcp is not None:
            self.mcp.stop()
        self._mcp_started = False

    def list_models(self) -> list[ModelInfo]:
        ensure_server(self.server)
        return list_models(self.server)

    def list_tools(self) -> list[ToolInfo]:
        self._start_mcp()
        return self.tools.list_tools()

    def available_tool_definitions(self) -> list[dict[str, Any]]:
        self._start_mcp()
        return self.tools.tool_definitions()

    def chat(
        self,
        text: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        model: str | None = None,
        context_size: int | None = None,
        system_prompt: str | None = None,
        use_tools: bool = True,
        max_tool_rounds: int = 8,
    ) -> ChatResult:
        self.start()
        selected_model = model or self.model
        if not selected_model:
            raise ValueError("A model name is required")

        thread = list(messages or [])
        if system_prompt:
            thread.insert(0, {"role": "system", "content": system_prompt})
        thread.append({"role": "user", "content": text})

        prompt_tokens = 0
        completion_tokens = 0
        tool_results: list[dict[str, Any]] = []
        final_message: dict[str, Any] = {}
        final_reason = ""
        final_tps = 0.0
        rounds = 0

        for rounds in range(1, max_tool_rounds + 1):
            result = self._chat_once(
                selected_model,
                thread,
                tools=self.available_tool_definitions() if use_tools else [],
                context_size=context_size if context_size is not None else self.context_size,
            )
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            final_tps = result.tokens_per_second
            final_message = result.message
            final_reason = result.finish_reason
            if not result.tool_calls:
                return ChatResult(
                    text=_message_text(final_message),
                    message=final_message,
                    finish_reason=final_reason,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    tool_calls=[],
                    tool_results=tool_results,
                    rounds=rounds,
                    tokens_per_second=final_tps,
                )

            thread.append({"role": "assistant", "content": final_message.get("content") or "", "tool_calls": _raw_tool_calls(final_message)})
            for call in result.tool_calls:
                output = self.call_tool(call.name, call.arguments)
                content = tool_result_to_text(output)
                tool_results.append({"tool": call.name, "arguments": call.arguments, "result": output})
                thread.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content})

        thread.append({"role": "user", "content": "Use the tool results above to answer the original request now."})
        result = self._chat_once(selected_model, thread, tools=[], context_size=context_size or self.context_size)
        return ChatResult(
            text=_message_text(result.message),
            message=result.message,
            finish_reason=result.finish_reason,
            prompt_tokens=prompt_tokens + result.prompt_tokens,
            completion_tokens=completion_tokens + result.completion_tokens,
            tool_calls=[],
            tool_results=tool_results,
            rounds=rounds + 1,
            tokens_per_second=result.tokens_per_second,
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.tools.can_call_locally(name):
            return self.tools.call(name, arguments)
        if self.mcp is not None and self.mcp.has_tool(name):
            return self.mcp.call_tool(name, arguments)
        raise KeyError(f"No executor is available for tool: {name}")

    def _chat_once(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        context_size: int | None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if context_size is not None:
            payload["options"] = {"num_ctx": context_size}

        started = time.monotonic()
        body = request_json(
            self.server.chat_completions_url,
            method="POST",
            payload=payload,
            headers=self.server.headers,
            timeout=self.server.timeout_seconds,
        )
        elapsed = max(0.001, time.monotonic() - started)
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"Chat response had no choices: {body}")
        choice = choices[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        parsed_calls = _parse_tool_calls(message)
        completion = int(usage.get("completion_tokens") or 0)
        return ChatResult(
            text=_message_text(message),
            message=message,
            finish_reason=str(choice.get("finish_reason") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=completion,
            tool_calls=parsed_calls,
            tool_results=[],
            rounds=1,
            tokens_per_second=(completion / elapsed) if completion else 0.0,
        )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    for key in ("thinking", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_tool_calls(message: dict[str, Any]) -> list[LLMToolCall]:
    calls: list[LLMToolCall] = []
    for index, item in enumerate(_raw_tool_calls(message)):
        function = item.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        raw_args = function.get("arguments") or {}
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                arguments = {"_argument_parse_error": str(exc), "_raw_arguments": raw_args}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            arguments = {}
        calls.append(LLMToolCall(id=str(item.get("id") or f"tool_call_{index}"), name=name, arguments=arguments))
    return calls


def _raw_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls") or []
    return calls if isinstance(calls, list) else []
