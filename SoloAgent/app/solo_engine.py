# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# SoloAgent orchestration layer: copied skill catalog, local tool registration, MCP merge and SoloLLM chat.

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import threading
import time
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable


APP_DIR = Path(__file__).resolve().parent
SOLO_AGENT_ROOT = APP_DIR.parent
SOLO_ROOT = SOLO_AGENT_ROOT.parent
SOLO_LLM_ROOT = SOLO_ROOT / "SoloLLM"
for path in (APP_DIR, SOLO_LLM_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from llm_client import set_active_llm  # noqa: E402
from input_layer.slash_command_context import SlashCommandContext  # noqa: E402
from input_layer.slash_commands import command_descriptions  # noqa: E402
from input_layer.slash_commands import command_names  # noqa: E402
from input_layer.slash_commands import handle as handle_slash  # noqa: E402
from scratchpad import get_store as get_scratchpad_store  # noqa: E402
from scratchpad import scratch_clear  # noqa: E402
from scratchpad import scratch_save  # noqa: E402
from session_runtime import bind_session  # noqa: E402
from solo_llm import LLMServerConfig  # noqa: E402
from solo_llm import SoloLLMClient  # noqa: E402
from solo_llm import ToolRegistry  # noqa: E402
from solo_llm.mcp_client import MCPToolClient  # noqa: E402
from solochat_client import AGENT_CHAT_EXTERNAL_ID  # noqa: E402
from solochat_client import SoloChatClient  # noqa: E402
from utils.workspace_utils import get_schedules_dir  # noqa: E402


@dataclass
class SkillEntry:
    name: str
    kind: str
    purpose: str
    module_path: str
    functions: list[str]
    path: str
    available: bool
    error: str = ""


@dataclass
class AgentRun:
    id: int
    created_at: str
    prompt: str
    response: str
    backend: str
    host: str
    model: str
    context_size: int | None
    use_tools: bool
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    rounds: int
    tool_results: list[dict[str, Any]]
    error: str = ""


class SoloAgentEngine:
    def __init__(self, *, config: dict[str, Any], data_root: Path) -> None:
        self.config = config
        self.data_root = data_root
        self.runs_path = data_root / "runs.json"
        self._runs: list[AgentRun] = self._load_runs()
        self._last_run_id = max((run.id for run in self._runs), default=0)
        self._active_prompts: dict[int, dict[str, Any]] = {}
        self._active_prompts_lock = threading.RLock()
        self._queue_stop_event = threading.Event()
        self._queue_thread: threading.Thread | None = None
        self._queue_worker_lock = threading.Lock()
        self.registry = ToolRegistry()
        self.skills: list[SkillEntry] = []
        self.chat = SoloChatClient(base_url=self._solochat_base_url(), external_id=AGENT_CHAT_EXTERNAL_ID)
        self._chat_error = ""
        self._ensure_agent_chat()
        self._load_skills()

    def start_queue_worker(self) -> None:
        if self._queue_thread is not None and self._queue_thread.is_alive():
            return
        self._queue_stop_event.clear()
        self._queue_thread = threading.Thread(target=self._queue_worker_loop, name="soloagent-queue-worker", daemon=True)
        self._queue_thread.start()

    def stop_queue_worker(self) -> None:
        self._queue_stop_event.set()
        if self._queue_thread is not None:
            self._queue_thread.join(timeout=3.0)

    def _queue_worker_loop(self) -> None:
        self._append_worker_log("queue worker started")
        while not self._queue_stop_event.is_set():
            try:
                processed = self.process_next_queued_prompt()
            except Exception as exc:
                processed = False
                self._append_worker_log(f"queue worker error: {exc}")
            if not processed:
                self._queue_stop_event.wait(1.0)
        self._append_worker_log("queue worker stopped")

    def process_next_queued_prompt(self) -> bool:
        if not self._queue_worker_lock.acquire(blocking=False):
            return False
        try:
            event = self.chat.claim_next_event(claimed_by="agent")
            if not event:
                return False
            try:
                self._run_claimed_event(event)
            except Exception as exc:
                self._append_worker_log(f"queued event failed before completion: {exc}")
                event_id = int(event.get("id") or 0)
                if event_id > 0:
                    try:
                        self.chat.complete_event(event_id, status="failed")
                    except Exception as complete_exc:
                        self._append_worker_log(f"queued event completion failed: {complete_exc}")
            return True
        finally:
            self._queue_worker_lock.release()

    def snapshot(self) -> dict[str, Any]:
        return {
            "service": "SoloAgent",
            "dataRoot": str(self.data_root),
            "skills": [asdict(skill) for skill in self.skills],
            "tools": [asdict(tool) for tool in self.registry.list_tools()],
            "recentRuns": [asdict(run) for run in self._runs[-20:]][::-1],
            "runningLog": self._running_log(),
            "defaults": self.defaults(),
            "agentChat": self.agent_chat_snapshot(),
            "queuedPrompts": self.queued_prompts_snapshot(),
        }

    def agent_chat_snapshot(self) -> dict[str, Any]:
        try:
            detail = self.chat.detail()
            self._chat_error = ""
            return {
                "externalId": AGENT_CHAT_EXTERNAL_ID,
                "conversation": detail.get("conversation"),
                "messages": detail.get("messages") or [],
                "error": "",
            }
        except Exception as exc:
            self._chat_error = str(exc)
            return {
                "externalId": AGENT_CHAT_EXTERNAL_ID,
                "conversation": None,
                "messages": [],
                "error": self._chat_error,
            }

    def queued_prompts_snapshot(self) -> dict[str, Any]:
        entries = self._active_prompt_entries()
        try:
            entries.extend(self.chat.queued_prompts())
            entries = _dedupe_queue_entries(entries)
            self._chat_error = ""
            return {
                "count": len(entries),
                "entries": entries,
                "error": "",
            }
        except Exception as exc:
            self._chat_error = str(exc)
            entries = _dedupe_queue_entries(entries)
            return {
                "count": len(entries),
                "entries": entries,
                "error": self._chat_error,
            }

    def defaults(self) -> dict[str, Any]:
        service = (self.config.get("services") or {}).get("soloagent") or {}
        llm = service.get("llm") if isinstance(service.get("llm"), dict) else {}
        return {
            "backend": str(llm.get("backend") or service.get("backend") or "ollama"),
            "host": str(llm.get("host") or service.get("host") or ""),
            "model": str(llm.get("model") or service.get("model") or ""),
            "contextSize": int(llm.get("contextSize") or service.get("contextSize") or 8192),
            "maxToolRounds": int(llm.get("maxToolRounds") or service.get("maxToolRounds") or 8),
        }

    def list_models(self, *, backend: str, host: str) -> list[dict[str, Any]]:
        server = LLMServerConfig.from_backend(backend or "ollama", host or None)
        client = SoloLLMClient(server=server)
        return [asdict(model) for model in client.list_models()]

    def completions(self) -> dict[str, Any]:
        defaults = self.defaults()
        try:
            models = [item["id"] for item in self.list_models(backend=defaults["backend"], host=defaults["host"])]
        except Exception:
            models = []
        try:
            detail = self.chat.detail()
            conversation = detail.get("conversation") if isinstance(detail, dict) else {}
            input_history = conversation.get("input_history") if isinstance(conversation, dict) else []
        except Exception:
            input_history = []
        return {
            "commands": command_names(),
            "descriptions": command_descriptions(),
            "input_history": input_history if isinstance(input_history, list) else [],
            "task_names": _task_names(),
            "models": models,
            "test_files": [],
            "sessions": [],
        }

    def run(self, request: dict[str, Any]) -> AgentRun:
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Prompt cannot be empty.")

        defaults = self.defaults()
        backend = str(request.get("backend") or defaults["backend"])
        host = str(request.get("host") or defaults["host"])
        model = str(request.get("model") or defaults["model"])
        context_size = _optional_int(request.get("contextSize"), defaults["contextSize"])
        max_rounds = max(1, _optional_int(request.get("maxToolRounds"), defaults["maxToolRounds"]) or 8)
        use_tools = bool(request.get("useTools", True))

        if prompt.startswith("/"):
            return self._run_slash_command(
                prompt=prompt,
                request=request,
                backend=backend,
                host=host,
                model=model,
                context_size=context_size,
                use_tools=use_tools,
            )

        run_id = self._next_run_id()
        created_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        self._start_active_prompt(run_id, prompt, created_at=created_at)
        messages = self._chat_thread_for_run()
        conversation_id = self._append_chat_user(prompt)
        session_id = _session_id_for_conversation(conversation_id)
        if conversation_id is not None:
            self._restore_conversation_scratchpad(conversation_id, session_id)
        set_active_llm(model=model, context_size=context_size, backend=backend, host=host)
        try:
            with bind_session(session_id):
                client = SoloLLMClient(
                    server=LLMServerConfig.from_backend(backend, host or None),
                    model=model,
                    context_size=context_size,
                    tools=self.registry,
                    mcp=self._create_mcp_client(),
                )
                result = client.chat(
                    prompt,
                    messages=messages,
                    system_prompt=self._system_prompt(),
                    use_tools=use_tools,
                    max_tool_rounds=max_rounds,
                )
            run = AgentRun(
                id=run_id,
                created_at=created_at,
                prompt=prompt,
                response=result.text,
                backend=backend,
                host=host,
                model=model,
                context_size=context_size,
                use_tools=use_tools,
                elapsed_seconds=round(time.monotonic() - started, 3),
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                rounds=result.rounds,
                tool_results=result.tool_results,
            )
        except Exception as exc:
            run = AgentRun(
                id=run_id,
                created_at=created_at,
                prompt=prompt,
                response="",
                backend=backend,
                host=host,
                model=model,
                context_size=context_size,
                use_tools=use_tools,
                elapsed_seconds=round(time.monotonic() - started, 3),
                prompt_tokens=0,
                completion_tokens=0,
                rounds=0,
                tool_results=[],
                error=str(exc),
            )
        self._runs.append(run)
        self._runs = self._runs[-100:]
        self._save_runs()
        self._append_run_log(run)
        self._append_chat_agent(run.response if not run.error else run.error, failed=bool(run.error))
        if conversation_id is not None:
            self._sync_conversation_scratchpad(conversation_id, session_id)
        self._finish_active_prompt(run_id)
        return run

    def _run_claimed_event(self, event: dict[str, Any]) -> None:
        event_id = int(event.get("id") or 0)
        conversation_id = int(event.get("conversation_id") or 0)
        event_type = str(event.get("event_type") or "")
        if event_id <= 0:
            return
        if event_type != "response_needed" or conversation_id <= 0:
            self.chat.complete_event(event_id, status="completed")
            return

        detail = self.chat.conversation_detail(conversation_id)
        conversation = detail.get("conversation") if isinstance(detail, dict) else {}
        messages = detail.get("messages") if isinstance(detail, dict) else []
        prompt = _latest_inbound_content(messages if isinstance(messages, list) else [])
        if not prompt:
            self.chat.complete_event(event_id, status="completed")
            return

        defaults = self.defaults()
        backend = str(defaults["backend"])
        host = str(defaults["host"])
        model = str(defaults["model"])
        context_size = _optional_int(defaults["contextSize"], 8192)
        max_rounds = max(1, _optional_int(defaults["maxToolRounds"], 8) or 8)
        use_tools = True

        run_id = self._next_run_id()
        created_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        session_id = _session_id_for_conversation(conversation_id)
        self._restore_scratchpad_from_record(conversation if isinstance(conversation, dict) else {}, session_id)
        self._start_active_prompt(
            run_id,
            prompt,
            created_at=created_at,
            event_id=event_id,
            conversation_id=conversation_id,
            conversation_name=_conversation_name(conversation if isinstance(conversation, dict) else {}),
            status="claimed",
        )
        set_active_llm(model=model, context_size=context_size, backend=backend, host=host)
        try:
            with bind_session(session_id):
                thread = _without_latest_user_prompt(self.chat.conversation_llm_thread(conversation_id), prompt)
                client = SoloLLMClient(
                    server=LLMServerConfig.from_backend(backend, host or None),
                    model=model,
                    context_size=context_size,
                    tools=self.registry,
                    mcp=self._create_mcp_client(),
                )
                result = client.chat(
                    prompt,
                    messages=thread,
                    system_prompt=self._system_prompt(),
                    use_tools=use_tools,
                    max_tool_rounds=max_rounds,
                )
            run = AgentRun(
                id=run_id,
                created_at=created_at,
                prompt=prompt,
                response=result.text,
                backend=backend,
                host=host,
                model=model,
                context_size=context_size,
                use_tools=use_tools,
                elapsed_seconds=round(time.monotonic() - started, 3),
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                rounds=result.rounds,
                tool_results=result.tool_results,
            )
        except Exception as exc:
            run = AgentRun(
                id=run_id,
                created_at=created_at,
                prompt=prompt,
                response="",
                backend=backend,
                host=host,
                model=model,
                context_size=context_size,
                use_tools=use_tools,
                elapsed_seconds=round(time.monotonic() - started, 3),
                prompt_tokens=0,
                completion_tokens=0,
                rounds=0,
                tool_results=[],
                error=str(exc),
            )

        try:
            self._runs.append(run)
            self._runs = self._runs[-100:]
            self._save_runs()
            self._append_run_log(run)
            self._sync_conversation_scratchpad(conversation_id, session_id)
            try:
                self.chat.append_message(
                    conversation_id,
                    direction="outbound",
                    content=run.response if not run.error else run.error,
                    sender_display="SoloAgent",
                    status="failed" if run.error else "sent",
                    queue_response=False,
                )
            finally:
                self.chat.complete_event(event_id, status="failed" if run.error else "completed")
        finally:
            self._finish_active_prompt(run_id)

    def _run_slash_command(
        self,
        *,
        prompt: str,
        request: dict[str, Any],
        backend: str,
        host: str,
        model: str,
        context_size: int | None,
        use_tools: bool,
    ) -> AgentRun:
        run_id = self._next_run_id()
        created_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        output_lines: list[str] = []
        self._start_active_prompt(run_id, prompt, created_at=created_at)
        conversation_id = self._append_chat_user(prompt)
        session_id = _session_id_for_conversation(conversation_id)
        if conversation_id is not None:
            self._restore_conversation_scratchpad(conversation_id, session_id)

        def _output(text: str, _level: str = "info") -> None:
            output_lines.append(str(text))

        error = ""
        try:
            with bind_session(session_id):
                handled = handle_slash(prompt, SlashCommandContext(engine=self, request=request, output=_output))
            if not handled:
                output_lines.append(f"Unknown command: {prompt.split()[0]}")
        except Exception as exc:
            error = str(exc)

        defaults = self.defaults()
        run = AgentRun(
            id=run_id,
            created_at=created_at,
            prompt=prompt,
            response="\n".join(output_lines) if output_lines else ("Run failed." if error else "(done)"),
            backend=str(defaults.get("backend") or backend),
            host=str(defaults.get("host") or host),
            model=str(defaults.get("model") or model),
            context_size=_optional_int(defaults.get("contextSize"), context_size),
            use_tools=use_tools,
            elapsed_seconds=round(time.monotonic() - started, 3),
            prompt_tokens=0,
            completion_tokens=0,
            rounds=0,
            tool_results=[],
            error=error,
        )
        self._runs.append(run)
        self._runs = self._runs[-100:]
        self._save_runs()
        self._append_run_log(run)
        self._append_chat_agent(run.response if not run.error else run.error, failed=bool(run.error))
        if conversation_id is not None:
            self._sync_conversation_scratchpad(conversation_id, session_id)
        self._finish_active_prompt(run_id)
        return run

    def _start_active_prompt(
        self,
        run_id: int,
        prompt: str,
        *,
        created_at: str,
        event_id: int | str | None = None,
        conversation_id: int | None = None,
        conversation_name: str = "AgentChat",
        status: str = "active",
    ) -> None:
        with self._active_prompts_lock:
            self._active_prompts[run_id] = {
                "event_id": event_id or f"run-{run_id}",
                "conversation_id": conversation_id,
                "conversation_name": conversation_name,
                "prompt": prompt,
                "created_at": created_at,
                "priority": 0,
                "status": status,
            }

    def _finish_active_prompt(self, run_id: int) -> None:
        with self._active_prompts_lock:
            self._active_prompts.pop(run_id, None)

    def _active_prompt_entries(self) -> list[dict[str, Any]]:
        with self._active_prompts_lock:
            return [dict(entry) for entry in self._active_prompts.values()]

    def _solochat_base_url(self) -> str:
        network = self.config.get("network") if isinstance(self.config.get("network"), dict) else {}
        services = self.config.get("services") if isinstance(self.config.get("services"), dict) else {}
        service = services.get("solochat") if isinstance(services.get("solochat"), dict) else {}
        host = str(service.get("host") or network.get("host") or "127.0.0.1")
        port = int(service.get("port") or 9720)
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"http://{host}:{port}"

    def _ensure_agent_chat(self) -> None:
        try:
            self.chat.ensure_conversation()
            self._chat_error = ""
        except Exception as exc:
            self._chat_error = str(exc)

    def _chat_thread_for_run(self) -> list[dict[str, Any]]:
        try:
            thread = self.chat.llm_thread()
            self._chat_error = ""
            return thread
        except Exception as exc:
            self._chat_error = str(exc)
            return []

    def _append_chat_user(self, prompt: str) -> int | None:
        try:
            result = self.chat.append_user_message(prompt)
            self._chat_error = ""
            conversation = result.get("conversation") if isinstance(result, dict) else None
            if isinstance(conversation, dict):
                conversation_id = int(conversation.get("id") or 0)
                return conversation_id if conversation_id > 0 else None
        except Exception as exc:
            self._chat_error = str(exc)
        return None

    def _append_chat_agent(self, content: str, *, failed: bool = False) -> None:
        text = str(content or "").strip()
        if not text:
            text = "(empty response)"
        try:
            self.chat.append_agent_message(text, failed=failed)
            self._chat_error = ""
        except Exception as exc:
            self._chat_error = str(exc)

    def _restore_conversation_scratchpad(self, conversation_id: int, session_id: str) -> None:
        try:
            detail = self.chat.conversation_detail(conversation_id)
            conversation = detail.get("conversation") if isinstance(detail, dict) else {}
            self._restore_scratchpad_from_record(conversation if isinstance(conversation, dict) else {}, session_id)
            self._chat_error = ""
        except Exception as exc:
            self._chat_error = str(exc)

    def _restore_scratchpad_from_record(self, conversation: dict[str, Any], session_id: str) -> None:
        scratchpad = conversation.get("scratchpad") if isinstance(conversation, dict) else {}
        if not isinstance(scratchpad, dict):
            scratchpad = {}
        scratch_clear(session_id=session_id)
        for key, value in scratchpad.items():
            try:
                scratch_save(str(key), str(value), session_id=session_id)
            except Exception as exc:
                self._append_worker_log(f"could not restore scratchpad key {key!r}: {exc}")

    def _sync_conversation_scratchpad(self, conversation_id: int, session_id: str) -> None:
        try:
            self.chat.update_conversation(conversation_id, scratchpad=get_scratchpad_store(session_id=session_id))
            self._chat_error = ""
        except Exception as exc:
            self._chat_error = str(exc)
            self._append_worker_log(f"could not sync scratchpad for conversation {conversation_id}: {exc}")

    def _load_skills(self) -> None:
        for kind, root in (("skill", APP_DIR / "skills"), ("system", APP_DIR / "system_skills")):
            for skill_doc in sorted(root.glob("*/skill.md")):
                entry = self._load_skill_doc(skill_doc, kind)
                self.skills.append(entry)

    def _load_skill_doc(self, skill_doc: Path, kind: str) -> SkillEntry:
        text = skill_doc.read_text(encoding="utf-8-sig")
        name = _clean_heading(text) or skill_doc.parent.name
        purpose = _section(text, "Purpose")
        module_path = _module_path(text)
        functions = _function_names(text)
        entry = SkillEntry(
            name=name,
            kind=kind,
            purpose=purpose,
            module_path=module_path,
            functions=functions,
            path=str(skill_doc.relative_to(SOLO_ROOT)),
            available=False,
        )
        if not module_path or not functions:
            entry.error = "Workflow-only skill; no callable functions."
            return entry
        try:
            module = self._import_module(module_path)
            registered = []
            for function_name in functions:
                function = getattr(module, function_name, None)
                if not callable(function):
                    continue
                self.registry.register(
                    function_name,
                    function,
                    description=_function_description(function, purpose),
                    source=entry.name,
                )
                registered.append(function_name)
            entry.functions = registered
            entry.available = bool(registered)
            if not registered:
                entry.error = "No declared functions were found in the module."
        except Exception as exc:
            entry.error = str(exc)
        return entry

    def _import_module(self, module_path: str) -> Any:
        rel = module_path.replace("\\", "/").strip()
        if rel.startswith("SoloAgent/"):
            rel = rel[len("SoloAgent/") :]
        target = (SOLO_AGENT_ROOT / rel).resolve()
        if target != SOLO_AGENT_ROOT and SOLO_AGENT_ROOT not in target.parents:
            raise ValueError(f"Module path escapes SoloAgent: {module_path}")
        if not target.exists():
            raise FileNotFoundError(str(target))
        module_name = "soloagent_skill_" + re.sub(r"[^A-Za-z0-9_]", "_", str(target.relative_to(SOLO_AGENT_ROOT)))
        spec = importlib.util.spec_from_file_location(module_name, target)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot import {target}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _system_prompt(self) -> str:
        callable_skills = [skill for skill in self.skills if skill.available]
        workflow_skills = [skill for skill in self.skills if not skill.available and "Workflow-only" in skill.error]
        skill_lines = [
            f"- {skill.name}: {skill.purpose or 'No purpose provided'} Tools: {', '.join(skill.functions)}"
            for skill in callable_skills
        ]
        workflow_lines = [
            f"- {skill.name}: {skill.purpose or 'Workflow guidance only'}"
            for skill in workflow_skills
        ]
        return "\n".join([
            "You are SoloAgent, a local agent orchestrator in the Solo suite.",
            "Use tools when they provide fresher, computed, file-backed, or externally fetched information.",
            "Keep answers concise unless the user asks for depth. Explain tool failures plainly.",
            "Do not invent tool results. If a tool is unavailable, say what blocked it.",
            "",
            "Callable skills:",
            *skill_lines,
            "",
            "Workflow guidance skills:",
            *workflow_lines,
        ])

    def create_mcp_client(self) -> MCPToolClient | None:
        connections = _mcp_connections(self.config)
        if not connections:
            return None
        return MCPToolClient(connections)

    def _create_mcp_client(self) -> MCPToolClient | None:
        return self.create_mcp_client()

    def _load_runs(self) -> list[AgentRun]:
        if not self.runs_path.exists():
            return []
        try:
            raw = json.loads(self.runs_path.read_text(encoding="utf-8-sig"))
            return [AgentRun(**item) for item in raw if isinstance(item, dict)]
        except Exception:
            return []

    def _save_runs(self) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_path.write_text(json.dumps([asdict(run) for run in self._runs], indent=2), encoding="utf-8")

    def _next_run_id(self) -> int:
        with self._active_prompts_lock:
            self._last_run_id += 1
            return self._last_run_id

    def _append_run_log(self, run: AgentRun) -> None:
        logs_dir = self.data_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        status = "failed" if run.error else "complete"
        lines = [
            f"[{run.created_at}] run #{run.id} {status}",
            f"backend={run.backend} host={run.host or '-'} model={run.model or '-'} ctx={run.context_size or '-'} tools={run.use_tools}",
            f"prompt={run.prompt}",
        ]
        if run.error:
            lines.append(f"error={run.error}")
        else:
            lines.append(f"response={run.response}")
        for index, item in enumerate(run.tool_results, start=1):
            lines.append(f"tool[{index}] {item.get('tool')} args={json.dumps(item.get('arguments') or {}, default=str)}")
        lines.append("")
        with (logs_dir / "agent.log").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    def _append_worker_log(self, text: str) -> None:
        logs_dir = self.data_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        with (logs_dir / "agent.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{now}] {text}\n")

    def _running_log(self) -> str:
        logs_dir = self.data_root / "logs"
        parts: list[str] = []
        for name in ("agent.log", "service.out.log", "service.err.log"):
            path = logs_dir / name
            if not path.exists():
                continue
            text = _tail_text(path, 12000)
            if text.strip():
                parts.append(f"== {name} ==\n{text.rstrip()}")
        if parts:
            return "\n\n".join(parts)
        return "No agent log entries yet."


def _clean_heading(text: str) -> str:
    match = re.search(r"^\s*#\s+(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).replace(" Skill", "").strip()


def _section(text: str, name: str) -> str:
    pattern = rf"^##\s+{re.escape(name)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return ""
    lines = [line.strip() for line in match.group(1).strip().splitlines()]
    return " ".join(line for line in lines if line and not line.startswith("-"))


def _module_path(text: str) -> str:
    match = re.search(r"^- Module:\s+`?([^`\r\n]+)`?\s*$", text, re.MULTILINE)
    if not match:
        return ""
    value = match.group(1).strip()
    if value.startswith("("):
        return ""
    return value


def _function_names(text: str) -> list[str]:
    names: list[str] = []
    functions_section = _section_raw(text, "Interface")
    for match in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*)\s*\(", functions_section):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def _section_raw(text: str, name: str) -> str:
    pattern = rf"^##\s+{re.escape(name)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1) if match else ""


def _function_description(function: Callable[..., Any], fallback: str) -> str:
    doc = inspect.getdoc(function) or ""
    if doc:
        return doc.splitlines()[0]
    return fallback


def _optional_int(value: object, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe_queue_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        prompt = str(entry.get("prompt") or "").strip()
        conversation = str(entry.get("conversation_name") or entry.get("conversation_id") or "").strip()
        key = (conversation, prompt)
        if prompt and key in seen:
            continue
        if prompt:
            seen.add(key)
        result.append(entry)
    return result


def _conversation_name(conversation: dict[str, Any]) -> str:
    return str(
        conversation.get("subject")
        or conversation.get("external_id")
        or f"Conversation {conversation.get('id') or '?'}"
    ).strip()


def _session_id_for_conversation(conversation_id: int | None) -> str:
    if conversation_id is None or int(conversation_id or 0) <= 0:
        return "default"
    return f"solochat-conversation-{int(conversation_id)}"


def _latest_inbound_content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("direction") == "inbound":
            return str(message.get("content") or "").strip()
    return ""


def _without_latest_user_prompt(thread: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
    trimmed = list(thread)
    if not trimmed:
        return trimmed
    latest = trimmed[-1]
    if latest.get("role") == "user" and str(latest.get("content") or "").strip() == prompt.strip():
        return trimmed[:-1]
    return trimmed


def _mcp_connections(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = config.get("mcp_connections")
    if raw is None:
        raw = config.get("mcp_servers")
    if raw is None and isinstance(config.get("mcp"), dict):
        raw = config["mcp"].get("connections")
    return raw if isinstance(raw, list) else []


def _task_names() -> list[str]:
    names: list[str] = []
    schedules_dir = get_schedules_dir()
    if not schedules_dir.exists():
        return names
    for json_path in sorted(schedules_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            name = str(task.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _tail_text(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {path.name}: {exc}"
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]
