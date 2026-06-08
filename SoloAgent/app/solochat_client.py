# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Minimal HTTP client for SoloAgent's dedicated SoloChat conversation.

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


AGENT_CHAT_EXTERNAL_ID = "soloagent-agentchat"
AGENT_CHAT_SUBJECT = "AgentChat"


class SoloChatClient:
    def __init__(self, *, base_url: str, external_id: str = AGENT_CHAT_EXTERNAL_ID) -> None:
        self.base_url = base_url.rstrip("/")
        self.external_id = external_id

    def ensure_conversation(self) -> dict[str, Any]:
        existing = self._get_optional(f"/api/conversations/by-external-id/{self._quoted_id()}")
        if existing is not None:
            return existing
        return self._request_json(
            "/api/conversations",
            method="POST",
            payload={
                "channel_type": "service",
                "profile": "admin",
                "subject": AGENT_CHAT_SUBJECT,
                "external_id": self.external_id,
                "protected": True,
                "background_context": "Dedicated SoloAgent conversation. User prompts are inbound; SoloAgent responses are outbound.",
            },
        )

    def llm_thread(self) -> list[dict[str, Any]]:
        self.ensure_conversation()
        data = self._request_json(f"/api/conversations/by-external-id/{self._quoted_id()}/llm-thread")
        thread = data.get("thread") if isinstance(data, dict) else []
        return thread if isinstance(thread, list) else []

    def append_user_message(self, content: str) -> dict[str, Any]:
        result = self._append_message(
            direction="inbound",
            content=content,
            sender_display="SoloAgent User",
            queue_response=False,
        )
        conversation = result.get("conversation") if isinstance(result, dict) else None
        conversation_id = int(conversation.get("id") or 0) if isinstance(conversation, dict) else 0
        if conversation_id > 0:
            self.append_input_history(conversation_id, content)
        return result

    def append_agent_message(self, content: str, *, failed: bool = False) -> dict[str, Any]:
        return self._append_message(
            direction="outbound",
            content=content,
            sender_display="SoloAgent",
            status="failed" if failed else "sent",
        )

    def append_input_history(self, conversation_id: int, text: str) -> None:
        self._request_json(
            f"/api/conversations/{conversation_id}/input-history",
            method="PATCH",
            payload={"text": text},
        )

    def detail(self) -> dict[str, Any]:
        self.ensure_conversation()
        return self._request_json(f"/api/conversations/by-external-id/{self._quoted_id()}/detail")

    def conversation_detail(self, conversation_id: int) -> dict[str, Any]:
        return self._request_json(f"/api/conversations/{int(conversation_id)}/detail")

    def update_conversation(
        self,
        conversation_id: int,
        *,
        scratchpad: dict[str, Any] | None = None,
        datasets: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if scratchpad is not None:
            payload["scratchpad"] = scratchpad
        if datasets is not None:
            payload["datasets"] = datasets
        return self._request_json(
            f"/api/conversations/{int(conversation_id)}",
            method="PATCH",
            payload=payload,
        )

    def conversation_llm_thread(self, conversation_id: int) -> list[dict[str, Any]]:
        data = self._request_json(f"/api/conversations/{int(conversation_id)}/llm-thread")
        thread = data.get("thread") if isinstance(data, dict) else []
        return thread if isinstance(thread, list) else []

    def append_message(
        self,
        conversation_id: int,
        *,
        direction: str,
        content: str,
        sender_display: str,
        status: str = "received",
        queue_response: bool = True,
    ) -> dict[str, Any]:
        return self._request_json(
            f"/api/conversations/{int(conversation_id)}/messages",
            method="POST",
            payload={
                "direction": direction,
                "content": content,
                "sender_display": sender_display,
                "status": status,
                "queue_response": queue_response,
            },
        )

    def claim_next_event(self, *, claimed_by: str = "agent") -> dict[str, Any] | None:
        result = self._request_json(f"/api/events/next?claimed_by={urllib.parse.quote(claimed_by, safe='')}")
        return result if isinstance(result, dict) and result else None

    def complete_event(self, event_id: int, *, status: str = "completed") -> dict[str, Any]:
        return self._request_json(
            f"/api/events/{int(event_id)}/complete",
            method="POST",
            payload={"status": status},
        )

    def queued_prompts(self, *, limit: int = 50) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for status in ("pending", "claimed"):
            status_events = self._request_json(f"/api/events?status={status}&limit={int(limit)}")
            if isinstance(status_events, list):
                events.extend(item for item in status_events if isinstance(item, dict))
        queued: list[dict[str, Any]] = []
        for event in events:
            if event.get("event_type") != "response_needed":
                continue
            status = str(event.get("status") or "").lower()
            if status not in ("pending", "claimed"):
                continue
            claimed_by = str(event.get("claimed_by") or "").lower()
            if status == "claimed" and claimed_by != "agent":
                continue
            conversation_id = int(event.get("conversation_id") or 0)
            if conversation_id <= 0:
                continue
            try:
                detail = self._request_json(f"/api/conversations/{conversation_id}/detail")
            except Exception:
                continue
            conversation = detail.get("conversation") if isinstance(detail, dict) else {}
            messages = detail.get("messages") if isinstance(detail, dict) else []
            prompt = _latest_inbound_content(messages if isinstance(messages, list) else [])
            if not prompt:
                continue
            queued.append({
                "event_id": event.get("id"),
                "conversation_id": conversation_id,
                "conversation_name": _conversation_name(conversation if isinstance(conversation, dict) else {}),
                "prompt": prompt,
                "created_at": event.get("created_at") or "",
                "priority": event.get("priority") or 0,
                "status": status,
            })
        return queued

    def _append_message(
        self,
        *,
        direction: str,
        content: str,
        sender_display: str,
        status: str = "received",
        queue_response: bool = True,
    ) -> dict[str, Any]:
        return self._request_json(
            f"/api/conversations/by-external-id/{self._quoted_id()}/messages",
            method="POST",
            payload={
                "channel_type": "service",
                "profile": "admin",
                "subject": AGENT_CHAT_SUBJECT,
                "protected": True,
                "direction": direction,
                "content": content,
                "sender_display": sender_display,
                "status": status,
                "queue_response": queue_response,
            },
        )

    def _quoted_id(self) -> str:
        return urllib.parse.quote(self.external_id, safe="")

    def _get_optional(self, path: str) -> dict[str, Any] | None:
        try:
            return self._request_json(path)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def _request_json(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=8.0) as response:  # noqa: S310 - local Solo service URL.
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _conversation_name(conversation: dict[str, Any]) -> str:
    return str(
        conversation.get("subject")
        or conversation.get("external_id")
        or f"Conversation {conversation.get('id') or '?'}"
    ).strip()


def _latest_inbound_content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("direction") == "inbound":
            return str(message.get("content") or "").strip()
    return ""
