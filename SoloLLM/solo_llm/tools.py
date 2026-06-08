# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Local tool registry and OpenAI tool-definition conversion for SoloLLM.

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any

from .types import ToolInfo


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        function: Callable[..., Any],
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        source: str = "local",
    ) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Tool name cannot be empty")
        schema = parameters or _schema_from_signature(function)
        self._tools[clean_name] = {
            "function": function,
            "description": description or inspect.getdoc(function) or "",
            "schema": _sanitize_schema(schema),
            "source": source,
            "available": True,
        }

    def merge_tool_definition(self, definition: dict[str, Any], *, source: str) -> None:
        function_def = definition.get("function") or {}
        name = str(function_def.get("name") or "").strip()
        if not name:
            return
        self._tools[name] = {
            "function": None,
            "description": str(function_def.get("description") or ""),
            "schema": _sanitize_schema(function_def.get("parameters") or {"type": "object", "properties": {}}),
            "source": source,
            "available": True,
        }

    def list_tools(self) -> list[ToolInfo]:
        return [
            ToolInfo(
                name=name,
                description=str(entry["description"]),
                source=str(entry["source"]),
                schema=dict(entry["schema"]),
                available=bool(entry.get("available", True)),
            )
            for name, entry in sorted(self._tools.items())
        ]

    def tool_definitions(self, *, include_remote: bool = True) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for name, entry in sorted(self._tools.items()):
            if entry["function"] is None and not include_remote:
                continue
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": entry["description"],
                        "parameters": entry["schema"],
                    },
                }
            )
        return definitions

    def can_call_locally(self, name: str) -> bool:
        entry = self._tools.get(name)
        return bool(entry and entry["function"] is not None)

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        entry = self._tools.get(name)
        if entry is None:
            raise KeyError(f"Unknown tool: {name}")
        function = entry["function"]
        if function is None:
            raise RuntimeError(f"Tool is registered but has no local executor: {name}")
        return function(**arguments)


def _schema_from_signature(function: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(function)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        properties[name] = {"type": _json_type(parameter.annotation)}
        if parameter.default is parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _json_type(annotation: Any) -> str:
    if annotation in (int, "int"):
        return "integer"
    if annotation in (float, "float"):
        return "number"
    if annotation in (bool, "bool"):
        return "boolean"
    if annotation in (dict, "dict"):
        return "object"
    if annotation in (list, "list"):
        return "array"
    return "string"


def _sanitize_schema(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {key: _sanitize_schema(item) for key, item in value.items() if item is not None}
        if "anyOf" in cleaned:
            non_null = [item for item in cleaned["anyOf"] if item != {"type": "null"}]
            if len(non_null) == 1:
                merged = dict(non_null[0])
                merged.update({key: item for key, item in cleaned.items() if key != "anyOf"})
                return merged
        return cleaned
    if isinstance(value, list):
        return [_sanitize_schema(item) for item in value if item is not None]
    return value


def tool_result_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, default=str)
