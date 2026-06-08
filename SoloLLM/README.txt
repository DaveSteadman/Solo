SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Documents the SoloLLM wrapper package and its command-line test harness.

SoloLLM is a small Python wrapper around local or OpenAI-compatible LLM servers.
It is not a service. Other Solo processes can import it and use it to:

- list available models
- set a model and context size
- send chat messages
- register local Python tools
- connect MCP servers
- list available local and MCP tools
- execute model-requested tool calls and return results to the model

Package:

SoloLLM/solo_llm

Convenience clients:

from solo_llm import OllamaClient
from solo_llm import LMStudioClient

ollama = OllamaClient(model="gpt-oss:20b", context_size=4096)
print(ollama.list_model_names())
print(ollama.generate_text("Say hello"))
print(ollama.chat("What time is it?").text)

lmstudio = LMStudioClient(model="local-model", context_size=4096)
print(lmstudio.list_model_names())
print(lmstudio.chat("Say hello").text)

Command-line tester:

python .\SoloLLM\testcode\llm_cli.py --list-models
python .\SoloLLM\testcode\llm_cli.py --list-tools
python .\SoloLLM\testcode\llm_cli.py --model gpt-oss:20b --ctx 4096 --prompt "Say hello"
python .\SoloLLM\testcode\llm_cli.py --model gpt-oss:20b --ctx 4096 --generate --prompt "Say hello"
python .\SoloLLM\testcode\llm_cli.py --running-models
python .\SoloLLM\testcode\llm_cli.py --model gpt-oss:20b --unload
python .\SoloLLM\testcode\llm_cli.py --backend lmstudio --model local-model --prompt "Say hello"
python .\SoloLLM\testcode\llm_cli.py --backend openai --model gpt-4.1-mini --prompt "Say hello"
python .\SoloLLM\testcode\llm_cli.py --model gpt-oss:20b --mcp-config .\Config\local.json --list-tools

Minimal embedding example:

from solo_llm import LLMServerConfig
from solo_llm import SoloLLMClient

client = SoloLLMClient(
    server=LLMServerConfig.from_backend("ollama"),
    model="gpt-oss:20b",
    context_size=4096,
)

result = client.chat("Write one sentence about SoloLLM.")
print(result.text)

MCP config shape:

{
  "mcp_connections": [
    {
      "name": "Example",
      "url": "http://localhost:8800/mcp",
      "transport": "streamable_http",
      "purpose": "Optional human-readable note",
      "enabled": true,
      "allowed_tools": [],
      "blocked_tools": []
    }
  ]
}
