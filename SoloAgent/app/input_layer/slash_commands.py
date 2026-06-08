# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Slash-command processor for SoloAgent. This ports the environment setup commands from
# KoreAgent's input_layer into SoloAgent's smaller stdlib HTTP architecture.

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Callable

from input_layer.slash_command_context import SlashCommandContext
from orchestration import get_sandbox_enabled
from orchestration import set_sandbox_enabled
from solo_llm import LLMServerConfig
from solo_llm.mcp_client import MCPToolClient
from system_skills.TaskManagement.task_management_skill import task_create
from system_skills.TaskManagement.task_management_skill import task_delete
from system_skills.TaskManagement.task_management_skill import task_get
from system_skills.TaskManagement.task_management_skill import task_list
from system_skills.TaskManagement.task_management_skill import task_set_enabled
from system_skills.TaskManagement.task_management_skill import task_set_prompt
from system_skills.TaskManagement.task_management_skill import task_set_schedule
from utils.workspace_utils import get_suite_local_file
from utils.workspace_utils import get_workspace_root


Handler = Callable[[str, SlashCommandContext], None]


def handle(text: str, ctx: SlashCommandContext) -> bool:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False

    parts = stripped.split(None, 1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    handler = _REGISTRY.get(command)
    if handler is None:
        ctx.output(f"Unknown command '{command}'. Type /help for available commands.", "dim")
        return True
    handler(arg, ctx)
    return True


def command_names() -> list[str]:
    return sorted(_REGISTRY)


def command_descriptions() -> dict[str, str]:
    return dict(_DESCRIPTIONS)


def _cmd_help(_arg: str, ctx: SlashCommandContext) -> None:
    ctx.output("Available slash commands:", "info")
    for name, description in sorted(_DESCRIPTIONS.items()):
        ctx.output(f"  {name:<18} {description}", "item")


def _cmd_version(_arg: str, ctx: SlashCommandContext) -> None:
    version_path = get_workspace_root() / "SoloCommonWebUI" / "framework" / "js" / "version.js"
    version = "unknown"
    try:
        match = re.search(r"version\s*=\s*[\"']([^\"']+)", version_path.read_text(encoding="utf-8-sig"))
        if match:
            version = match.group(1)
    except Exception:
        pass
    ctx.output(f"SoloAgent {version}", "info")


def _cmd_sandbox(arg: str, ctx: SlashCommandContext) -> None:
    sub = arg.strip().lower()
    if sub == "on":
        set_sandbox_enabled(True)
        ctx.output("Python sandbox enabled.", "success")
        return
    if sub == "off":
        set_sandbox_enabled(False)
        ctx.output("Python sandbox disabled.", "success")
        ctx.output("Warning: code snippets can now use unrestricted Python except blocked GUI modules.", "dim")
        return
    state = "on" if get_sandbox_enabled() else "off"
    ctx.output(f"Usage: /sandbox <on|off>  |  current: {state}", "dim")


def _cmd_tools(_arg: str, ctx: SlashCommandContext) -> None:
    tools = ctx.engine.registry.list_tools()
    if not tools:
        ctx.output("No tools available.", "dim")
        return
    ctx.output(f"{len(tools)} local tool(s) available:", "info")
    for tool in tools:
        params = ", ".join((tool.schema.get("properties") or {}).keys())
        signature = f"{tool.name}({params})"
        ctx.output(f"  {signature}", "item")
        if tool.description:
            ctx.output(f"    {tool.source}: {tool.description[:120]}", "dim")


def _cmd_mcp(arg: str, ctx: SlashCommandContext) -> None:
    sub = arg.strip().lower() or "status"
    if sub not in ("status", "reconnect"):
        ctx.output("Usage: /mcp [status|reconnect]", "dim")
        return

    client = ctx.engine.create_mcp_client()
    if client is None:
        ctx.output("No MCP connections configured.", "dim")
        return
    try:
        client.start()
        statuses = client.status()
        if not statuses:
            ctx.output("No MCP connections configured.", "dim")
            return
        for status in statuses:
            ok = "OK" if status.get("ok") else "FAIL"
            name = status.get("name") or "-"
            url = status.get("url") or "-"
            count = status.get("tool_count") or 0
            message = status.get("detail") or ""
            ctx.output(f"  {ok:<4} {name}  {url}  ({count} tool(s)) {message}", "item")
    except Exception as exc:
        ctx.output(f"MCP error: {exc}", "error")
    finally:
        client.stop()


def _cmd_llmserver(arg: str, ctx: SlashCommandContext) -> None:
    defaults = ctx.engine.defaults()
    if not arg:
        host = defaults["host"] or LLMServerConfig.from_backend(defaults["backend"]).base_url
        ctx.output(f"Current server: {defaults['backend']} @ {host}", "info")
        ctx.output("Usage: /llmserver <ollama|lmstudio|openai> <host-or-url>", "dim")
        return

    parts = arg.split(None, 1)
    backend = parts[0].lower()
    if backend not in ("ollama", "lmstudio", "openai") or len(parts) < 2:
        ctx.output("Usage: /llmserver <ollama|lmstudio|openai> <host-or-url>", "error")
        return

    host = parts[1].strip()
    try:
        server = LLMServerConfig.from_backend(backend, host or None)
        models = ctx.engine.list_models(backend=backend, host=server.base_url)
    except Exception as exc:
        ctx.output(f"Cannot reach {backend} at {host}: {exc}", "error")
        return

    llm = _agent_llm_config(ctx)
    old_backend = llm.get("backend") or defaults["backend"]
    old_host = llm.get("host") or defaults["host"]
    llm["backend"] = backend
    llm["host"] = server.base_url
    if models and llm.get("model") not in {item.get("id") for item in models}:
        llm["model"] = models[0]["id"]
    ctx.output(f"Server: {old_backend} @ {old_host or '-'} -> {backend} @ {server.base_url}", "success")
    if models:
        ctx.output(f"  {len(models)} model(s): {', '.join(item['id'] for item in models[:12])}", "item")


def _cmd_llmserverconfig(arg: str, ctx: SlashCommandContext) -> None:
    defaults = ctx.engine.defaults()
    if not arg:
        host = defaults["host"] or LLMServerConfig.from_backend(defaults["backend"]).base_url
        ctx.output(
            f"Model: {defaults['model'] or '(none)'}  |  ctx: {defaults['contextSize']:,}  |  "
            f"rounds: {defaults['maxToolRounds']}  |  backend: {defaults['backend']} @ {host}",
            "info",
        )
        ctx.output("Usage: /llmserverconfig model list | model <name> | ctx <n> | rounds <n>", "dim")
        return

    parts = arg.split(None, 1)
    first = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if first == "model":
        _llmserverconfig_model(rest, ctx)
        return
    if first in ("ctx", "context"):
        _set_int_llm_value(ctx, "contextSize", rest, 512, "Context window")
        return
    if first == "rounds":
        _set_int_llm_value(ctx, "maxToolRounds", rest, 1, "Max tool rounds")
        return
    ctx.output("Usage: /llmserverconfig model list | model <name> | ctx <n> | rounds <n>", "error")


def _llmserverconfig_model(arg: str, ctx: SlashCommandContext) -> None:
    defaults = ctx.engine.defaults()
    if not arg or arg.lower() == "list":
        try:
            models = ctx.engine.list_models(backend=defaults["backend"], host=defaults["host"])
        except Exception as exc:
            ctx.output(f"Error listing models: {exc}", "error")
            return
        ctx.output(f"{len(models)} model(s) available:", "info")
        active = defaults["model"]
        for model in models:
            marker = ">" if model["id"] == active else " "
            ctx.output(f"  {marker} {model['id']}", "item")
        return

    old = defaults["model"] or "(none)"
    llm = _agent_llm_config(ctx)
    llm["model"] = arg.strip()
    ctx.output(f"Model switched: {old} -> {llm['model']}", "success")


def _cmd_ctx(arg: str, ctx: SlashCommandContext) -> None:
    defaults = ctx.engine.defaults()
    parts = arg.split(None, 1)
    if not arg:
        ctx.output(f"Context window size: {defaults['contextSize']:,}", "info")
        return
    if parts[0].lower() == "size":
        value = parts[1].strip() if len(parts) > 1 else ""
    else:
        value = arg.strip()
    _set_int_llm_value(ctx, "contextSize", value, 512, "Context window")


def _cmd_rounds(arg: str, ctx: SlashCommandContext) -> None:
    _set_int_llm_value(ctx, "maxToolRounds", arg.strip(), 1, "Max tool rounds")


def _cmd_defaults(arg: str, ctx: SlashCommandContext) -> None:
    defaults = ctx.engine.defaults()
    if arg.strip().lower() == "set":
        local_path = get_suite_local_file()
        existing = _read_json(local_path)
        services = existing.setdefault("services", {})
        service = services.setdefault("soloagent", {})
        service["llm"] = defaults
        local_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ctx.output(f"Saved SoloAgent LLM defaults to {local_path}", "success")
        return
    ctx.output(f"Local config: {get_suite_local_file()}", "info")
    for key, value in defaults.items():
        ctx.output(f"  {key:<14} {value or '-'}", "item")
    ctx.output("Use /defaults set to save the current SoloAgent LLM defaults to local.json.", "dim")


def _cmd_stopmodel(arg: str, ctx: SlashCommandContext) -> None:
    defaults = ctx.engine.defaults()
    if defaults["backend"] != "ollama":
        ctx.output("Model unloading is only supported for Ollama. Use the model server UI for this backend.", "dim")
        return
    model = arg.strip() or defaults["model"]
    if not model:
        ctx.output("No model is configured. Usage: /stopmodel <name>", "error")
        return
    server = LLMServerConfig.from_backend("ollama", defaults["host"] or None)
    payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
    request = urllib.request.Request(
        f"{server.base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            response.read()
        ctx.output(f"Model unload requested: {model}", "success")
    except Exception as exc:
        ctx.output(f"Error stopping model: {exc}", "error")


def _cmd_tasks(_arg: str, ctx: SlashCommandContext) -> None:
    ctx.output(task_list(), "info")


def _cmd_task(arg: str, ctx: SlashCommandContext) -> None:
    parts = arg.split(None, 1)
    if not parts:
        ctx.output("Usage: /task <get|add|enable|disable|delete|schedule|prompt|run> ...", "dim")
        return
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub == "get":
        ctx.output(task_get(rest), "info")
        return
    if sub in ("enable", "disable"):
        ctx.output(task_set_enabled(rest, sub == "enable"), "success")
        return
    if sub == "delete":
        ctx.output(task_delete(rest), "success")
        return
    if sub == "add":
        add_parts = rest.split(None, 2)
        if len(add_parts) < 3:
            ctx.output("Usage: /task add <name> <minutes|HH:MM> <prompt>", "dim")
            return
        ctx.output(task_create(add_parts[0], add_parts[1], add_parts[2]), "success")
        return
    if sub == "schedule":
        set_parts = rest.split(None, 1)
        if len(set_parts) < 2:
            ctx.output("Usage: /task schedule <name> <minutes|HH:MM>", "dim")
            return
        ctx.output(task_set_schedule(set_parts[0], set_parts[1]), "success")
        return
    if sub == "prompt":
        set_parts = rest.split(None, 1)
        if len(set_parts) < 2:
            ctx.output("Usage: /task prompt <name> <prompt>", "dim")
            return
        ctx.output(task_set_prompt(set_parts[0], set_parts[1]), "success")
        return
    if sub == "run":
        ctx.output("SoloAgent does not have the KoreAgent scheduler queue yet; /task run is not implemented.", "dim")
        return
    ctx.output("Unknown sub-command. Use get, add, enable, disable, delete, schedule, prompt, or run.", "error")


def _set_int_llm_value(ctx: SlashCommandContext, key: str, raw: str, minimum: int, label: str) -> None:
    if not raw:
        current = ctx.engine.defaults().get(key)
        ctx.output(f"Usage: {label} <n>  |  current: {current}", "dim")
        return
    try:
        value = int(raw.replace(",", "").replace("_", ""))
    except ValueError:
        ctx.output(f"Invalid value '{raw}' - must be an integer.", "error")
        return
    if value < minimum:
        ctx.output(f"{label} must be at least {minimum}.", "error")
        return
    llm = _agent_llm_config(ctx)
    old = ctx.engine.defaults().get(key)
    llm[key] = value
    ctx.output(f"{label} changed: {old} -> {value}", "success")


def _agent_llm_config(ctx: SlashCommandContext) -> dict:
    services = ctx.engine.config.setdefault("services", {})
    service = services.setdefault("soloagent", {})
    llm = service.setdefault("llm", {})
    return llm


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_REGISTRY: dict[str, Handler] = {
    "/help": _cmd_help,
    "/version": _cmd_version,
    "/sandbox": _cmd_sandbox,
    "/tools": _cmd_tools,
    "/mcp": _cmd_mcp,
    "/llmserver": _cmd_llmserver,
    "/llmserverconfig": _cmd_llmserverconfig,
    "/ctx": _cmd_ctx,
    "/rounds": _cmd_rounds,
    "/defaults": _cmd_defaults,
    "/stopmodel": _cmd_stopmodel,
    "/tasks": _cmd_tasks,
    "/task": _cmd_task,
}

_DESCRIPTIONS: dict[str, str] = {
    "/help": "List available slash commands",
    "/version": "Show SoloAgent/common UI version",
    "/sandbox": "<on|off>  Enable or disable the Python code-execution sandbox",
    "/tools": "List local tools exposed to the model",
    "/mcp": "[status|reconnect]  Show MCP server status",
    "/llmserver": "<ollama|lmstudio|openai> <host-or-url>  Switch model server",
    "/llmserverconfig": "model list | model <name> | ctx <n> | rounds <n>",
    "/ctx": "[size] <n>  Set context window size",
    "/rounds": "<n>  Set max tool-call rounds per prompt",
    "/defaults": "Show current defaults; /defaults set saves them to Config/local.json",
    "/stopmodel": "[name]  Unload a running Ollama model",
    "/tasks": "List scheduled task JSON entries",
    "/task": "get|add|enable|disable|delete|schedule|prompt|run  Manage task JSON entries",
}
