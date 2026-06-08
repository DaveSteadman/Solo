# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Command-line tester for SoloLLM model selection, context size, chat, local tools and MCP tools.

from __future__ import annotations

import argparse
import datetime as _datetime
import os
import sys
from pathlib import Path
from typing import Any

SOLO_LLM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOLO_LLM_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from solo_llm import LMStudioClient
from solo_llm import OllamaClient
from solo_llm import SoloLLMClient
from solo_llm import ToolRegistry
from solo_llm.mcp_client import MCPToolClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send text to an LLM through the SoloLLM wrapper.")
    parser.add_argument("--backend", choices=("ollama", "lmstudio", "openai"), default="ollama")
    parser.add_argument("--host", default=None, help="Base host URL or hostname for the selected backend.")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="Bearer token for OpenAI-compatible servers.")
    parser.add_argument("--model", default="", help="Model id to use for chat.")
    parser.add_argument("--ctx", type=int, default=None, help="Context size to request when the backend supports it.")
    parser.add_argument("--prompt", default="", help="Single prompt to send. If omitted, starts interactive mode.")
    parser.add_argument("--system", default="", help="Optional system prompt.")
    parser.add_argument("--mcp-config", default="", help="JSON file containing mcp_connections or mcp.servers.")
    parser.add_argument("--list-models", action="store_true", help="List models and exit.")
    parser.add_argument("--list-tools", action="store_true", help="List local and MCP tools and exit.")
    parser.add_argument("--running-models", action="store_true", help="List Ollama models currently loaded in memory.")
    parser.add_argument("--unload", action="store_true", help="Unload the selected Ollama model and exit.")
    parser.add_argument("--generate", action="store_true", help="Use Ollama's /api/generate endpoint instead of chat completions.")
    parser.add_argument("--no-tools", action="store_true", help="Do not send tool definitions to the model.")
    return parser.parse_args()


def build_demo_tools() -> ToolRegistry:
    registry = ToolRegistry()

    def current_time() -> dict[str, str]:
        now = _datetime.datetime.now().astimezone()
        return {"iso": now.isoformat(timespec="seconds"), "timezone": str(now.tzinfo)}

    def echo_text(text: str) -> str:
        return text

    registry.register(
        "current_time",
        current_time,
        description="Return the current local date, time and timezone.",
        parameters={"type": "object", "properties": {}},
    )
    registry.register(
        "echo_text",
        echo_text,
        description="Return the provided text unchanged.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo."}},
            "required": ["text"],
        },
    )
    return registry


def print_models(client: SoloLLMClient) -> None:
    for model in client.list_models():
        print(f"{model.id}  [{model.source}]")


def print_tools(client: SoloLLMClient) -> None:
    for tool in client.list_tools():
        state = "available" if tool.available else "unavailable"
        print(f"{tool.name}  [{tool.source}; {state}]")
        if tool.description:
            print(f"  {tool.description}")
    if client.mcp is not None:
        for status in client.mcp.status():
            ok = "OK" if status["ok"] else "FAIL"
            print(f"MCP {ok} {status['name']} {status['url']} ({status['tool_count']} tools) {status['detail']}")


def run_once(client: SoloLLMClient, prompt: str, system_prompt: str, use_tools: bool) -> None:
    result = client.chat(prompt, system_prompt=system_prompt or None, use_tools=use_tools)
    print(result.text)
    if result.tool_results:
        print()
        print("Tool calls:")
        for item in result.tool_results:
            print(f"  {item['tool']}({item['arguments']})")
    print()
    print(f"rounds={result.rounds} prompt_tokens={result.prompt_tokens} completion_tokens={result.completion_tokens} tps={result.tokens_per_second:.1f}")


def interactive(client: SoloLLMClient, system_prompt: str, use_tools: bool) -> None:
    print("SoloLLM interactive tester. Blank line exits.")
    while True:
        prompt = input("> ").strip()
        if not prompt:
            return
        try:
            run_once(client, prompt, system_prompt, use_tools)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    mcp = MCPToolClient.from_config_file(args.mcp_config) if args.mcp_config else None
    if args.backend == "lmstudio":
        client: SoloLLMClient = LMStudioClient(
            host=args.host,
            model=args.model,
            context_size=args.ctx,
            tools=build_demo_tools(),
            mcp=mcp,
        )
    elif args.backend == "ollama":
        client = OllamaClient(
            host=args.host,
            model=args.model,
            context_size=args.ctx,
            tools=build_demo_tools(),
            mcp=mcp,
        )
    else:
        from solo_llm import LLMServerConfig

        server = LLMServerConfig.from_backend(args.backend, args.host, args.api_key)
        client = SoloLLMClient(
            server=server,
            model=args.model,
            context_size=args.ctx,
            tools=build_demo_tools(),
            mcp=mcp,
        )
    try:
        if args.list_models:
            print_models(client)
            return 0
        if args.list_tools:
            print_tools(client)
            return 0
        if args.running_models:
            if not isinstance(client, OllamaClient):
                print("--running-models is only available for --backend ollama", file=sys.stderr)
                return 2
            for item in client.running_models():
                print(item.get("name") or item.get("model") or item)
            return 0
        if args.unload:
            if not isinstance(client, OllamaClient):
                print("--unload is only available for --backend ollama", file=sys.stderr)
                return 2
            client.unload_model()
            print(f"Unloaded {args.model}")
            return 0
        if not args.model:
            print("Use --model for chat, or --list-models to inspect available models.", file=sys.stderr)
            return 2
        if args.generate:
            if not isinstance(client, OllamaClient):
                print("--generate is only available for --backend ollama", file=sys.stderr)
                return 2
            if not args.prompt:
                print("--generate requires --prompt", file=sys.stderr)
                return 2
            print(client.generate_text(args.prompt))
            return 0
        if args.prompt:
            run_once(client, args.prompt, args.system, not args.no_tools)
        else:
            interactive(client, args.system, not args.no_tools)
        return 0
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main())
