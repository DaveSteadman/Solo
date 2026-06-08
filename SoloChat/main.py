# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Launches the SoloChat conversation-state service and serves its JSON/web UI.

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
import traceback
from typing import Any
from typing import ClassVar
from typing import Iterator


SOLO_CHAT_ROOT = Path(__file__).resolve().parent
SOLO_ROOT = SOLO_CHAT_ROOT.parent
SOLO_DATA_ROOT = SOLO_ROOT / "SoloData"
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.sqlite import sqlite_connection  # noqa: E402
from common_utils.web import send_json  # noqa: E402
from common_utils.web import serve_bounded_file  # noqa: E402
from common_utils.web import serve_file  # noqa: E402


CONFIG_DIR = SOLO_ROOT / "Config"
FACTORY_DEFAULT_CONFIG = CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = CONFIG_DIR / "local.json"
UI_DIR = SOLO_CHAT_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
CLAIM_TIMEOUT_SECS = 600
CHANNEL_PROFILE_DEFAULTS: dict[str, str] = {
    "webchat": "admin",
    "service": "external",
    "external": "external",
}
FALLBACK_PROFILE = "external"
CLAIMABLE_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "agent": ("response_needed", "compress_needed", "conversation_closed"),
    "solollm": ("response_needed",),
    "solochat": ("outbound_ready", "conversation_deleted"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_type        TEXT    NOT NULL DEFAULT 'service',
    profile             TEXT    NOT NULL DEFAULT 'external'
                                CHECK(profile IN ('admin','external','readonly')),
    status              TEXT    NOT NULL DEFAULT 'active'
                                CHECK(status IN ('active','waiting_agent','agent_processing','archived','deleted')),
    subject             TEXT,
    protected           INTEGER NOT NULL DEFAULT 0,
    external_id         TEXT,
    thread_summary      TEXT    NOT NULL DEFAULT '',
    scratchpad          TEXT    NOT NULL DEFAULT '{}',
    datasets            TEXT    NOT NULL DEFAULT '{}',
    input_history       TEXT    NOT NULL DEFAULT '[]',
    background_context  TEXT    NOT NULL DEFAULT '',
    token_estimate      INTEGER NOT NULL DEFAULT 0,
    turn_count          INTEGER NOT NULL DEFAULT 0,
    last_activity_at    TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction        TEXT    NOT NULL CHECK(direction IN ('inbound','outbound')),
    content          TEXT    NOT NULL,
    sender_display   TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT 'received'
                             CHECK(status IN ('received','draft','sent','failed')),
    summarised       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    event_type       TEXT    NOT NULL
                             CHECK(event_type IN (
                                 'response_needed','outbound_ready','compress_needed',
                                 'conversation_closed','conversation_deleted'
                             )),
    status           TEXT    NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending','claimed','completed','failed')),
    claimed_by       TEXT,
    claimed_at       TEXT,
    priority         INTEGER NOT NULL DEFAULT 0,
    payload          TEXT    NOT NULL DEFAULT '{}',
    created_at       TEXT    NOT NULL,
    completed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_summarised ON messages(conversation_id, summarised);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_events_conv ON events(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status, last_activity_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_external_id ON conversations(external_id)
WHERE external_id IS NOT NULL;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SoloChat conversation-state service.")
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument("--host", default=None, help="Bind host.")
    parser.add_argument("--port", type=int, default=None, help="Bind port.")
    parser.add_argument("--dry-run", action="store_true", help="Show configuration without starting the server.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    return merge_dict(load_json(FACTORY_DEFAULT_CONFIG), load_json(LOCAL_CONFIG))


def resolve_solo_path(raw: object, default: str) -> Path:
    path = Path(str(raw or default))
    if path.is_absolute():
        return path.resolve()
    return (SOLO_ROOT / path).resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def query_int(params: dict[str, list[str]], name: str, default: int) -> int:
    return parse_int((params.get(name) or [str(default)])[0], default)


def query_text(params: dict[str, list[str]], name: str) -> str:
    return str((params.get(name) or [""])[0]).strip()


def blank_to_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def decode_json_value(raw_value: str, default: object, *, label: str) -> tuple[object, str | None]:
    try:
        return json.loads(raw_value or json.dumps(default)), None
    except json.JSONDecodeError as exc:
        detail = f"{label} JSON decode failed: {exc.msg} at line {exc.lineno} column {exc.colno}"
        print(f"[SoloChat] Warning: {detail}", flush=True)
        return default, detail


def decode_state_fields(record: dict[str, Any], *, label: str) -> None:
    raw_scratchpad = str(record.get("scratchpad") or "{}")
    scratchpad, scratchpad_error = decode_json_value(raw_scratchpad, {}, label=f"{label} scratchpad")
    record["scratchpad"] = scratchpad if isinstance(scratchpad, dict) else {}
    if scratchpad_error:
        record["scratchpad_raw"] = raw_scratchpad
        record["scratchpad_parse_error"] = scratchpad_error

    raw_datasets = str(record.get("datasets") or "{}")
    datasets, datasets_error = decode_json_value(raw_datasets, {}, label=f"{label} datasets")
    record["datasets"] = datasets if isinstance(datasets, dict) else {}
    if datasets_error:
        record["datasets_raw"] = raw_datasets
        record["datasets_parse_error"] = datasets_error

    raw_input_history = str(record.get("input_history") or "[]")
    input_history, _input_history_error = decode_json_value(raw_input_history, [], label=f"{label} input_history")
    record["input_history"] = input_history if isinstance(input_history, list) else []


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def default_profile(channel_type: str) -> str:
    return CHANNEL_PROFILE_DEFAULTS.get(channel_type, FALLBACK_PROFILE)


def is_protected_subject(subject: str | None, external_id: str | None = None) -> int:
    normalized = (subject or "").strip().lower()
    if normalized in ("", "new conversation"):
        return 0
    ext = (external_id or "").strip().lower()
    if ext.startswith("webchat_") and normalized == f"webchat {ext[8:]}":
        return 0
    return 1


def claimable_event_types_for_consumer(claimed_by: str) -> tuple[str, ...] | None:
    return CLAIMABLE_EVENT_TYPES.get((claimed_by or "").strip().lower())


class SoloChatStore:
    def __init__(self, chat_root: Path, data_root: Path) -> None:
        self.chat_root = chat_root
        self.data_root = data_root
        self.log_dir = self.chat_root / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.chat_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.chat_root / "solochat.db"
        self.init_db()

    def log_path(self) -> Path:
        return self.log_dir / "solochat.log"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with sqlite_connection(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection

    def init_db(self) -> None:
        with self.connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
            if columns and "external_id" not in columns:
                connection.execute("ALTER TABLE conversations ADD COLUMN external_id TEXT")
            if columns and "input_history" not in columns:
                connection.execute("ALTER TABLE conversations ADD COLUMN input_history TEXT NOT NULL DEFAULT '[]'")
            if columns and "datasets" not in columns:
                connection.execute("ALTER TABLE conversations ADD COLUMN datasets TEXT NOT NULL DEFAULT '{}'")
            if columns and "protected" not in columns:
                connection.execute("ALTER TABLE conversations ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
            connection.executescript(SCHEMA)

    def snapshot(self) -> dict[str, Any]:
        conversations = self.conversation_list(limit=50)
        return {
            "service": {
                "label": "SoloChat",
                "status": "ok",
                "metrics": {
                    "conversations": sum(self.conversation_counts().values()),
                    "messages": self.message_count(),
                    "events": sum(self.event_counts().values()),
                },
            },
            "paths": {
                "soloRoot": str(SOLO_ROOT),
                "dataRoot": str(self.data_root),
                "chatRoot": str(self.chat_root),
                "dbPath": str(self.db_path),
            },
            "counts": {
                "conversations": self.conversation_counts(),
                "events": self.event_counts(),
            },
            "recentConversations": conversations,
        }

    def status(self) -> dict[str, Any]:
        counts = self.conversation_counts()
        return {
            "service": "SoloChat",
            "status": "ok",
            "conversations": sum(counts.values()),
            "events": sum(self.event_counts().values()),
            "dataRoot": str(self.chat_root),
        }

    def conversation_create(
        self,
        channel_type: str,
        subject: str | None = None,
        background_context: str = "",
        profile: str | None = None,
        external_id: str | None = None,
        protected: bool | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        selected_profile = profile or default_profile(channel_type)
        protected_value = int(protected) if protected is not None else is_protected_subject(subject, external_id)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversations (
                    channel_type, profile, status, subject, protected, external_id,
                    thread_summary, scratchpad, datasets, input_history,
                    background_context, token_estimate, turn_count,
                    last_activity_at, created_at, updated_at
                )
                VALUES (?, ?, 'active', ?, ?, ?, '', '{}', '{}', '[]', ?, 0, 0, ?, ?, ?)
                """,
                (channel_type, selected_profile, subject, protected_value, external_id, background_context, now, now, now),
            )
            conversation_id = int(cursor.lastrowid)
        return self.conversation_get(conversation_id) or {}

    def conversation_get_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE external_id = ? LIMIT 1", (external_id,)).fetchone()
        record = row_to_dict(row)
        if record is not None:
            decode_state_fields(record, label=f"conversation external_id={external_id}")
        return record

    def conversation_get(self, conversation_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        record = row_to_dict(row)
        if record is not None:
            decode_state_fields(record, label=f"conversation {conversation_id}")
        return record

    def conversation_list(
        self,
        *,
        status: str | None = None,
        channel_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM conversations WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if channel_type:
            query += " AND channel_type = ?"
            params.append(channel_type)
        query += " ORDER BY last_activity_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        records = [dict(row) for row in rows]
        for item in records:
            decode_state_fields(item, label=f"conversation {item.get('id')}")
        return records

    def conversation_get_with_messages(self, conversation_id: int) -> dict[str, Any] | None:
        conversation = self.conversation_get(conversation_id)
        if conversation is None:
            return None
        conversation["messages"] = self.message_list(conversation_id=conversation_id, summarised=0, limit=1000)
        return conversation

    def conversation_get_detail(self, conversation_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            conversation_row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if conversation_row is None:
                return None
            message_rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 1000",
                (conversation_id,),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT * FROM events WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 200",
                (conversation_id,),
            ).fetchall()
        conversation = dict(conversation_row)
        decode_state_fields(conversation, label=f"conversation {conversation_id}")
        messages = [dict(row) for row in message_rows]
        return {
            "conversation": conversation,
            "messages": messages,
            "events": [dict(row) for row in event_rows],
            "llm_thread": self.build_llm_thread(conversation, messages),
        }

    def conversation_get_turns_by_external_id(self, external_id: str) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            conversation_row = connection.execute("SELECT id FROM conversations WHERE external_id = ? LIMIT 1", (external_id,)).fetchone()
            if conversation_row is None:
                return None
            rows = connection.execute(
                "SELECT direction, content, sender_display, status, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 1000",
                (conversation_row["id"],),
            ).fetchall()
        return [dict(row) for row in rows]

    def conversation_llm_thread(self, conversation_id: int) -> list[dict[str, Any]]:
        conversation = self.conversation_get(conversation_id)
        if conversation is None:
            return []
        messages = self.message_list(conversation_id=conversation_id, summarised=0, limit=1000)
        return self.build_llm_thread(conversation, messages)

    def build_llm_thread(self, conversation: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        thread: list[dict[str, Any]] = []
        background_context = str(conversation.get("background_context") or "").strip()
        if background_context:
            thread.append({"role": "system", "content": background_context})
        thread_summary = str(conversation.get("thread_summary") or "").strip()
        if thread_summary:
            thread.append({"role": "system", "content": f"Conversation summary:\n{thread_summary}"})
        for message in messages:
            if int(message.get("summarised") or 0) == 1:
                continue
            thread.append({
                "role": "user" if message.get("direction") == "inbound" else "assistant",
                "content": str(message.get("content") or ""),
            })
        return thread

    def conversation_update(
        self,
        conversation_id: int,
        *,
        status: str | None = None,
        subject: str | None = None,
        protected: bool | None = None,
        thread_summary: str | None = None,
        scratchpad: dict[str, Any] | None = None,
        datasets: dict[str, Any] | None = None,
        background_context: str | None = None,
        token_estimate: int | None = None,
        turn_count: int | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        fields = ["updated_at = ?", "last_activity_at = ?"]
        params: list[Any] = [now, now]
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if subject is not None:
            fields.append("subject = ?")
            params.append(subject)
            if is_protected_subject(subject):
                fields.append("protected = 1")
        if protected is not None:
            fields.append("protected = ?")
            params.append(int(protected))
        if thread_summary is not None:
            fields.append("thread_summary = ?")
            params.append(thread_summary)
        if scratchpad is not None:
            fields.append("scratchpad = ?")
            params.append(json.dumps(scratchpad))
        if datasets is not None:
            fields.append("datasets = ?")
            params.append(json.dumps(datasets))
        if background_context is not None:
            fields.append("background_context = ?")
            params.append(background_context)
        if token_estimate is not None:
            fields.append("token_estimate = ?")
            params.append(token_estimate)
        if turn_count is not None:
            fields.append("turn_count = ?")
            params.append(turn_count)
        params.append(conversation_id)
        with self.connect() as connection:
            connection.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", params)
        return self.conversation_get(conversation_id)

    def conversation_get_input_history(self, conversation_id: int) -> list[Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT input_history FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            return []
        try:
            decoded = json.loads(row["input_history"] or "[]")
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []

    def conversation_set_input_history(self, conversation_id: int, history: list[Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE conversations SET input_history = ?, updated_at = ? WHERE id = ?",
                (json.dumps(history), utc_now(), conversation_id),
            )

    def conversation_delete(self, conversation_id: int) -> bool:
        with self.connect() as connection:
            connection.execute("DELETE FROM events WHERE conversation_id = ?", (conversation_id,))
            cursor = connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cursor.rowcount > 0

    def message_append(
        self,
        conversation_id: int,
        direction: str,
        content: str,
        sender_display: str = "",
        status: str = "received",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (conversation_id, direction, content, sender_display, status, summarised, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (conversation_id, direction, content, sender_display, status, now),
            )
            message_id = int(cursor.lastrowid)
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            turn_count = int(connection.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0])
            connection.execute(
                "UPDATE conversations SET updated_at = ?, last_activity_at = ?, turn_count = ? WHERE id = ?",
                (now, now, turn_count, conversation_id),
            )
        return dict(row) if row is not None else {}

    def message_list(
        self,
        *,
        conversation_id: int,
        summarised: int | None = None,
        direction: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM messages WHERE conversation_id = ?"
        params: list[Any] = [conversation_id]
        if summarised is not None:
            query += " AND summarised = ?"
            params.append(summarised)
        if direction:
            query += " AND direction = ?"
            params.append(direction)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def message_update(self, message_id: int, *, status: str | None = None, summarised: int | None = None) -> dict[str, Any] | None:
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if summarised is not None:
            fields.append("summarised = ?")
            params.append(summarised)
        if not fields:
            return None
        params.append(message_id)
        with self.connect() as connection:
            connection.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id = ?", params)
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row is not None else None

    def latest_message_tx(self, connection: sqlite3.Connection, conversation_id: int) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT id, direction, created_at FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()

    def conversation_has_unanswered_inbound(self, conversation_id: int) -> bool:
        with self.connect() as connection:
            row = self.latest_message_tx(connection, conversation_id)
        return row is not None and row["direction"] == "inbound"

    def ensure_response_needed_event(self, conversation_id: int) -> bool:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self.latest_message_tx(connection, conversation_id)
            if latest is None or latest["direction"] != "inbound":
                connection.execute("COMMIT")
                return False
            existing = connection.execute(
                """
                SELECT 1 FROM events
                WHERE conversation_id = ?
                  AND event_type = 'response_needed'
                  AND status IN ('pending', 'claimed')
                  AND created_at >= ?
                LIMIT 1
                """,
                (conversation_id, latest["created_at"]),
            ).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return False
            connection.execute(
                """
                INSERT INTO events (conversation_id, event_type, status, priority, payload, created_at)
                VALUES (?, 'response_needed', 'pending', 0, '{}', ?)
                """,
                (conversation_id, now),
            )
            connection.execute(
                "UPDATE conversations SET status = 'waiting_agent', updated_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, conversation_id),
            )
            connection.execute("COMMIT")
        return True

    def clear_pending_response_needed_events(self, conversation_id: int) -> int:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE events
                SET status = 'completed', completed_at = ?
                WHERE conversation_id = ?
                  AND event_type = 'response_needed'
                  AND status IN ('pending', 'claimed')
                """,
                (now, conversation_id),
            )
            connection.execute(
                "UPDATE conversations SET status = 'active', updated_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, conversation_id),
            )
        return int(cursor.rowcount)

    def event_create(
        self,
        conversation_id: int | None,
        event_type: str,
        priority: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (conversation_id, event_type, status, priority, payload, created_at)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (conversation_id, event_type, priority, json.dumps(payload or {}), now),
            )
            event_id = int(cursor.lastrowid)
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row is not None else {}

    def event_list(
        self,
        *,
        conversation_id: int | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(f"SELECT * FROM events {where} ORDER BY priority DESC, created_at DESC LIMIT ?", params).fetchall()
        return [dict(row) for row in rows]

    def event_claim_next(self, claimed_by: str) -> dict[str, Any] | None:
        now = utc_now()
        claimable_types = claimable_event_types_for_consumer(claimed_by)
        type_clause = ""
        type_params: list[str] = []
        if claimable_types:
            placeholders = ", ".join("?" for _ in claimable_types)
            type_clause = f" AND event_type IN ({placeholders})"
            type_params = list(claimable_types)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            while True:
                row = connection.execute(
                    f"""
                    SELECT * FROM events
                    WHERE status = 'pending'{type_clause}
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    """,
                    type_params,
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None

                event_id = int(row["id"])
                conversation_id = row["conversation_id"]
                latest = self.latest_message_tx(connection, conversation_id) if conversation_id is not None else None
                if row["event_type"] == "response_needed" and (latest is None or latest["direction"] != "inbound"):
                    connection.execute(
                        "UPDATE events SET status = 'completed', completed_at = ? WHERE id = ?",
                        (now, event_id),
                    )
                    continue

                connection.execute(
                    "UPDATE events SET status = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ?",
                    (claimed_by, now, event_id),
                )
                if row["event_type"] == "response_needed" and conversation_id is not None:
                    connection.execute(
                        "UPDATE conversations SET status = 'agent_processing', updated_at = ?, last_activity_at = ? WHERE id = ?",
                        (now, now, conversation_id),
                    )
                connection.execute("COMMIT")
                updated = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                return dict(updated) if updated is not None else None

    def event_complete(self, event_id: int, status: str = "completed") -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("UPDATE events SET status = ?, completed_at = ? WHERE id = ?", (status, now, event_id))
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row is not None else None

    def release_stale_claims(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=CLAIM_TIMEOUT_SECS)).isoformat()
        now = utc_now()
        with self.connect() as connection:
            stale_conversations = [
                row["conversation_id"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT conversation_id FROM events
                    WHERE status = 'claimed' AND claimed_at < ?
                      AND event_type = 'response_needed' AND conversation_id IS NOT NULL
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            cursor = connection.execute(
                "UPDATE events SET status = 'pending', claimed_by = NULL, claimed_at = NULL WHERE status = 'claimed' AND claimed_at < ?",
                (cutoff,),
            )
            for conversation_id in stale_conversations:
                latest = self.latest_message_tx(connection, int(conversation_id))
                new_status = "waiting_agent" if latest is not None and latest["direction"] == "inbound" else "active"
                connection.execute(
                    "UPDATE conversations SET status = ?, updated_at = ?, last_activity_at = ? WHERE id = ?",
                    (new_status, now, now, conversation_id),
                )
        return int(cursor.rowcount)

    def conversation_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS n FROM conversations GROUP BY status").fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def event_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS n FROM events GROUP BY status").fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def message_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM messages").fetchone()
        return int(row[0] if row is not None else 0)

    def append_message_by_external_id(self, external_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        conversation = self.conversation_get_by_external_id(external_id)
        if conversation is None:
            conversation = self.conversation_create(
                channel_type=str(payload.get("channel_type") or "external"),
                subject=blank_to_none(payload.get("subject")) or external_id,
                background_context=str(payload.get("background_context") or ""),
                profile=blank_to_none(payload.get("profile")),
                external_id=external_id,
                protected=payload.get("protected") if isinstance(payload.get("protected"), bool) else None,
            )
        message = self.message_append(
            conversation_id=int(conversation["id"]),
            direction=str(payload.get("direction") or "inbound"),
            content=str(payload.get("content") or ""),
            sender_display=str(payload.get("sender_display") or ""),
            status=str(payload.get("status") or "received"),
        )
        if message.get("direction") == "inbound" and payload.get("queue_response") is not False:
            self.ensure_response_needed_event(int(conversation["id"]))
        else:
            self.clear_pending_response_needed_events(int(conversation["id"]))
        return {
            "conversation": self.conversation_get(int(conversation["id"])),
            "message": message,
        }


def build_handler(store: SoloChatStore):
    class ChatHandler(BaseHTTPRequestHandler):
        store_ref: ClassVar[SoloChatStore] = store

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query)
            try:
                if path in ("", "/", "/ui"):
                    serve_file(self, UI_DIR / "index.html")
                    return
                if path == "/status":
                    send_json(self, self.store_ref.status())
                    return
                if path == "/api/snapshot":
                    send_json(self, self.store_ref.snapshot())
                    return
                if path == "/api/conversations":
                    send_json(
                        self,
                        self.store_ref.conversation_list(
                            status=query_text(params, "status") or None,
                            channel_type=query_text(params, "channel_type") or None,
                            limit=query_int(params, "limit", 50),
                            offset=query_int(params, "offset", 0),
                        ),
                    )
                    return
                if path.startswith("/api/conversations/by-external-id/"):
                    remainder = path.removeprefix("/api/conversations/by-external-id/")
                    external_id, suffix = split_remainder(remainder)
                    conversation = self.store_ref.conversation_get_by_external_id(urllib.parse.unquote(external_id))
                    if conversation is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                        return
                    if suffix == "":
                        send_json(self, conversation)
                        return
                    if suffix == "/turns":
                        send_json(self, {"messages": self.store_ref.conversation_get_turns_by_external_id(urllib.parse.unquote(external_id)) or []})
                        return
                    if suffix == "/detail":
                        detail = self.store_ref.conversation_get_detail(int(conversation["id"]))
                        send_json(self, detail)
                        return
                    if suffix == "/llm-thread":
                        send_json(self, {"thread": self.store_ref.conversation_llm_thread(int(conversation["id"]))})
                        return
                if path.startswith("/api/conversations/"):
                    remainder = path.removeprefix("/api/conversations/")
                    conversation_id_text, suffix = split_remainder(remainder)
                    conversation_id = parse_int(conversation_id_text, -1)
                    if conversation_id <= 0:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Invalid conversation id")
                        return
                    if suffix == "":
                        conversation = self.store_ref.conversation_get_with_messages(conversation_id)
                        if conversation is None:
                            self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                            return
                        send_json(self, conversation)
                        return
                    if suffix == "/detail":
                        detail = self.store_ref.conversation_get_detail(conversation_id)
                        if detail is None:
                            self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                            return
                        send_json(self, detail)
                        return
                    if suffix == "/messages":
                        if self.store_ref.conversation_get(conversation_id) is None:
                            self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                            return
                        send_json(
                            self,
                            self.store_ref.message_list(
                                conversation_id=conversation_id,
                                summarised=parse_optional_int(query_text(params, "summarised")),
                                direction=query_text(params, "direction") or None,
                                limit=query_int(params, "limit", 200),
                            ),
                        )
                        return
                    if suffix == "/input-history":
                        if self.store_ref.conversation_get(conversation_id) is None:
                            self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                            return
                        send_json(self, {"entries": self.store_ref.conversation_get_input_history(conversation_id)})
                        return
                    if suffix == "/llm-thread":
                        if self.store_ref.conversation_get(conversation_id) is None:
                            self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                            return
                        send_json(self, {"thread": self.store_ref.conversation_llm_thread(conversation_id)})
                        return
                if path == "/api/events":
                    send_json(
                        self,
                        self.store_ref.event_list(
                            conversation_id=parse_optional_int(query_text(params, "conversation_id")),
                            status=query_text(params, "status") or None,
                            limit=query_int(params, "limit", 200),
                        ),
                    )
                    return
                if path == "/api/events/next":
                    claimed_by = query_text(params, "claimed_by")
                    if not claimed_by:
                        self.send_error(HTTPStatus.BAD_REQUEST, "claimed_by is required")
                        return
                    event = self.store_ref.event_claim_next(claimed_by)
                    if event is None:
                        self.send_response(HTTPStatus.NO_CONTENT)
                        self.end_headers()
                        return
                    result = dict(event)
                    if result.get("conversation_id"):
                        result["conversation"] = self.store_ref.conversation_get_with_messages(int(result["conversation_id"]))
                    send_json(self, result)
                    return
                if path.startswith("/ui/"):
                    relative_path = path.removeprefix("/ui/")
                    if "." not in Path(relative_path).name:
                        serve_file(self, UI_DIR / "index.html")
                        return
                    serve_bounded_file(self, UI_DIR, relative_path)
                    return
                if path.startswith("/common/"):
                    serve_bounded_file(self, COMMON_UI_DIR, path.removeprefix("/common/"))
                    return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == "/api/conversations":
                    payload = self.read_json()
                    created = self.store_ref.conversation_create(
                        channel_type=str(payload.get("channel_type") or "service"),
                        subject=blank_to_none(payload.get("subject")),
                        background_context=str(payload.get("background_context") or ""),
                        profile=blank_to_none(payload.get("profile")),
                        external_id=blank_to_none(payload.get("external_id")),
                        protected=payload.get("protected") if isinstance(payload.get("protected"), bool) else None,
                    )
                    send_json(self, created, status=HTTPStatus.CREATED)
                    return
                if parsed.path.startswith("/api/conversations/by-external-id/") and parsed.path.endswith("/messages"):
                    external_id = urllib.parse.unquote(parsed.path.removeprefix("/api/conversations/by-external-id/").removesuffix("/messages"))
                    result = self.store_ref.append_message_by_external_id(external_id, self.read_json())
                    send_json(self, result, status=HTTPStatus.CREATED)
                    return
                if parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/messages"):
                    conversation_id = parse_int(parsed.path.removeprefix("/api/conversations/").removesuffix("/messages").strip("/"), -1)
                    if self.store_ref.conversation_get(conversation_id) is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                        return
                    payload = self.read_json()
                    message = self.store_ref.message_append(
                        conversation_id=conversation_id,
                        direction=str(payload.get("direction") or "inbound"),
                        content=str(payload.get("content") or ""),
                        sender_display=str(payload.get("sender_display") or ""),
                        status=str(payload.get("status") or "received"),
                    )
                    if message.get("direction") == "inbound" and payload.get("queue_response") is not False:
                        self.store_ref.ensure_response_needed_event(conversation_id)
                    else:
                        self.store_ref.clear_pending_response_needed_events(conversation_id)
                    send_json(self, message, status=HTTPStatus.CREATED)
                    return
                if parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/queue-response"):
                    conversation_id = parse_int(parsed.path.removeprefix("/api/conversations/").removesuffix("/queue-response").strip("/"), -1)
                    if self.store_ref.conversation_get(conversation_id) is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                        return
                    queued = self.store_ref.ensure_response_needed_event(conversation_id)
                    send_json(
                        self,
                        {
                            "queued": queued,
                            "conversation": self.store_ref.conversation_get(conversation_id),
                            "events": self.store_ref.event_list(conversation_id=conversation_id, limit=20),
                        },
                        status=HTTPStatus.CREATED if queued else HTTPStatus.OK,
                    )
                    return
                if parsed.path == "/api/events":
                    payload = self.read_json()
                    send_json(
                        self,
                        self.store_ref.event_create(
                            conversation_id=parse_optional_int(str(payload.get("conversation_id") or "")),
                            event_type=str(payload.get("event_type") or "response_needed"),
                            priority=parse_int(str(payload.get("priority") or "0"), 0),
                            payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
                        ),
                        status=HTTPStatus.CREATED,
                    )
                    return
                if parsed.path.startswith("/api/events/") and parsed.path.endswith("/complete"):
                    event_id = parse_int(parsed.path.removeprefix("/api/events/").removesuffix("/complete").strip("/"), -1)
                    result = self.store_ref.event_complete(event_id, status=str(self.read_json().get("status") or "completed"))
                    if result is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Event not found")
                        return
                    send_json(self, result)
                    return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/input-history"):
                    conversation_id = parse_int(parsed.path.removeprefix("/api/conversations/").removesuffix("/input-history").strip("/"), -1)
                    if self.store_ref.conversation_get(conversation_id) is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                        return
                    text = str(self.read_json().get("text") or "").strip()
                    if not text:
                        self.send_error(HTTPStatus.BAD_REQUEST, "text cannot be empty")
                        return
                    entries = [item for item in self.store_ref.conversation_get_input_history(conversation_id) if item != text]
                    entries.append(text)
                    if len(entries) > 32:
                        entries = entries[-32:]
                    self.store_ref.conversation_set_input_history(conversation_id, entries)
                    send_json(self, {"entries": entries})
                    return
                if parsed.path.startswith("/api/conversations/"):
                    conversation_id = parse_int(parsed.path.removeprefix("/api/conversations/").strip("/"), -1)
                    payload = self.read_json()
                    updated = self.store_ref.conversation_update(
                        conversation_id,
                        status=blank_to_none(payload.get("status")),
                        subject=payload.get("subject") if "subject" in payload else None,
                        protected=payload.get("protected") if isinstance(payload.get("protected"), bool) else None,
                        thread_summary=payload.get("thread_summary") if "thread_summary" in payload else None,
                        scratchpad=payload.get("scratchpad") if isinstance(payload.get("scratchpad"), dict) else None,
                        datasets=payload.get("datasets") if isinstance(payload.get("datasets"), dict) else None,
                        background_context=payload.get("background_context") if "background_context" in payload else None,
                        token_estimate=parse_optional_int(str(payload.get("token_estimate") or "")),
                        turn_count=parse_optional_int(str(payload.get("turn_count") or "")),
                    )
                    if updated is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                        return
                    send_json(self, updated)
                    return
                if parsed.path.startswith("/api/messages/"):
                    message_id = parse_int(parsed.path.removeprefix("/api/messages/").strip("/"), -1)
                    payload = self.read_json()
                    updated = self.store_ref.message_update(
                        message_id,
                        status=blank_to_none(payload.get("status")),
                        summarised=parse_optional_int(str(payload.get("summarised") or "")),
                    )
                    if updated is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Message not found")
                        return
                    send_json(self, updated)
                    return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.startswith("/api/conversations/"):
                conversation_id = parse_int(parsed.path.removeprefix("/api/conversations/").strip("/"), -1)
                if not self.store_ref.conversation_delete(conversation_id):
                    self.send_error(HTTPStatus.NOT_FOUND, "Conversation not found")
                    return
                send_json(self, {"ok": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def read_json(self) -> dict[str, Any]:
            length = parse_int(self.headers.get("Content-Length", "0"), 0)
            if length <= 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}

    return ChatHandler


def split_remainder(remainder: str) -> tuple[str, str]:
    text = remainder.strip("/")
    if "/" not in text:
        return text, ""
    head, tail = text.split("/", 1)
    return head, f"/{tail}"


def parse_optional_int(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def reaper_loop(store: SoloChatStore, stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        try:
            released = store.release_stale_claims()
            if released:
                print(f"SoloChat reaper released {released} stale event claim(s)", flush=True)
        except Exception as exc:
            logging.exception("SoloChat reaper error")
            print(f"SoloChat reaper error: {exc}", flush=True)


def serve(store: SoloChatStore, host: str, port: int, stop_event: threading.Event) -> None:
    httpd = ThreadingHTTPServer((host, port), build_handler(store))
    httpd.timeout = 0.5
    while not stop_event.is_set():
        try:
            httpd.handle_request()
        except OSError:
            logging.exception("SoloChat request-loop OSError")
            continue
        except Exception:
            logging.exception("SoloChat request-loop fatal error")
            continue
    httpd.server_close()


def print_status(store: SoloChatStore, host: str, port: int) -> None:
    snapshot = store.snapshot()
    print("SoloChat status")
    print(f"  url          http://{host}:{port}/")
    print(f"  data         {snapshot['paths']['chatRoot']}")
    print(f"  conversations {snapshot['service']['metrics']['conversations']}")
    print(f"  messages     {snapshot['service']['metrics']['messages']}")
    print(f"  events       {snapshot['service']['metrics']['events']}")


def main() -> int:
    args = parse_args()
    config = load_config()
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    solochat = services.get("solochat") if isinstance(services.get("solochat"), dict) else {}
    host = args.host or str(solochat.get("host") or network.get("host") or "127.0.0.1")
    port = int(args.port or solochat.get("port") or 9720)
    data_root = resolve_solo_path(paths.get("dataRoot"), "./Data")
    chat_root = (data_root / "SoloChat").resolve()
    store = SoloChatStore(chat_root, data_root)
    logging.basicConfig(
        filename=str(store.log_path()),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("SoloChat starting on %s:%s", host, port)

    if args.command == "status" or args.dry_run:
        print_status(store, host, port)
        return 0

    stop_event = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    reaper = threading.Thread(target=reaper_loop, args=(store, stop_event), daemon=True)
    reaper.start()

    print(f"SoloChat: http://{host}:{port}/", flush=True)
    print(f"SoloChat status: http://{host}:{port}/status", flush=True)
    print(f"SoloChat data: {chat_root}", flush=True)
    try:
        serve(store, host, port, stop_event)
    except Exception:
        logging.exception("SoloChat main loop crashed")
        traceback.print_exc()
        return 1
    logging.info("SoloChat stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
