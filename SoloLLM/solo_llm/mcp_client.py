# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Optional synchronous facade over MCP servers so SoloLLM can discover and call remote tools.

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


class MCPToolClient:
    def __init__(self, connections: list[dict[str, Any]] | None = None) -> None:
        self._connections = [_normalize_connection(item) for item in (connections or []) if item.get("url")]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tool_defs: list[dict[str, Any]] = []
        self._tool_index: dict[str, dict[str, Any]] = {}
        self._status: list[dict[str, Any]] = []

    @classmethod
    def from_config_file(cls, path: str | Path) -> "MCPToolClient":
        config_path = Path(path)
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        raw = data.get("mcp_connections")
        if raw is None:
            raw = data.get("mcp_servers")
        if raw is None and isinstance(data.get("mcp"), dict):
            raw = data["mcp"].get("connections")
        if not isinstance(raw, list):
            raw = []
        return cls(raw)

    def start(self) -> None:
        if not MCP_AVAILABLE or not self._connections:
            self._status = [
                _status_entry(item, 0, False, "mcp package not installed" if not MCP_AVAILABLE else "not connected")
                for item in self._connections
            ]
            return
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=_run_loop, args=(self._loop,), daemon=True, name="solo-mcp")
            self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._enumerate(), self._loop)
        self._tool_defs, self._tool_index, self._status = future.result(timeout=max(5.0, len(self._connections) * 6.0))

    def stop(self) -> None:
        if self._loop is not None:
            future = asyncio.run_coroutine_threadsafe(_drain_async_cleanup(), self._loop)
            try:
                future.result(timeout=2.0)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._loop = None
            self._thread = None

    def tool_definitions(self) -> list[dict[str, Any]]:
        return list(self._tool_defs)

    def status(self) -> list[dict[str, Any]]:
        return list(self._status)

    def has_tool(self, name: str) -> bool:
        return name in self._tool_index

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._loop is None:
            raise RuntimeError("MCP client is not running")
        entry = self._tool_index.get(name)
        if entry is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        future = asyncio.run_coroutine_threadsafe(_call_tool_async(entry, name, arguments), self._loop)
        return future.result(timeout=30.0)

    async def _enumerate(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
        tool_defs: list[dict[str, Any]] = []
        tool_index: dict[str, dict[str, Any]] = {}
        statuses: list[dict[str, Any]] = []
        for connection in self._connections:
            try:
                defs, index = await asyncio.wait_for(_list_tools_async(connection), timeout=5.0)
                duplicate_names = sorted(set(tool_index).intersection(index))
                defs = [item for item in defs if item.get("function", {}).get("name") not in duplicate_names]
                for duplicate_name in duplicate_names:
                    index.pop(duplicate_name, None)
                tool_defs.extend(defs)
                tool_index.update(index)
                statuses.append(_status_entry(connection, len(index), True, "ok"))
            except Exception as exc:
                statuses.append(_status_entry(connection, 0, False, str(exc) or exc.__class__.__name__))
        return tool_defs, tool_index, statuses


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


async def _drain_async_cleanup() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.get_running_loop().shutdown_asyncgens()


def _normalize_connection(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw.get("name") or raw.get("server") or raw.get("url") or "").strip(),
        "url": str(raw.get("url") or "").strip(),
        "transport": str(raw.get("transport") or "streamable_http").strip().lower(),
        "purpose": str(raw.get("purpose") or "").strip(),
        "allowed_tools": set(raw.get("allowed_tools") or []),
        "blocked_tools": set(raw.get("blocked_tools") or []),
        "expected_prefix": str(raw.get("expected_prefix") or raw.get("tool_prefix") or "").strip(),
    }


def _status_entry(connection: dict[str, Any], tool_count: int, ok: bool, detail: str) -> dict[str, Any]:
    return {
        "name": connection.get("name") or connection.get("url"),
        "url": connection.get("url"),
        "transport": connection.get("transport"),
        "purpose": connection.get("purpose"),
        "tool_count": tool_count,
        "ok": ok,
        "detail": detail,
    }


@asynccontextmanager
async def _open_transport(connection: dict[str, Any]):
    transport = connection.get("transport") or "streamable_http"
    url = connection["url"]
    if transport == "sse":
        async with sse_client(url, timeout=5.0, sse_read_timeout=30.0) as (read, write):
            yield read, write
        return
    if transport not in ("streamable_http", "streamable-http", "http"):
        raise ValueError(f"Unsupported MCP transport: {transport}")
    async with streamablehttp_client(url) as (read, write, _):
        yield read, write


async def _list_tools_async(connection: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    defs: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    allowed = connection.get("allowed_tools") or set()
    blocked = connection.get("blocked_tools") or set()
    expected_prefix = connection.get("expected_prefix") or ""
    async with _open_transport(connection) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
    for tool in result.tools:
        if allowed and tool.name not in allowed:
            continue
        if tool.name in blocked:
            continue
        if expected_prefix and not tool.name.startswith(expected_prefix):
            continue
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        definition = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": _sanitize_schema(schema),
            },
        }
        defs.append(definition)
        index[tool.name] = dict(connection)
    return defs, index


async def _call_tool_async(connection: dict[str, Any], name: str, arguments: dict[str, Any]) -> Any:
    async with _open_transport(connection) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
    text_parts = [item.text for item in result.content if hasattr(item, "text") and item.text]
    if text_parts:
        text = "\n".join(text_parts)
        return f"Error: {text}" if result.isError else text
    return [getattr(item, "__dict__", str(item)) for item in result.content]


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
