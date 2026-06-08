SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Documents the SoloChat conversation-state service.

SoloChat is the shared conversation store for Solo services.
It keeps conversation records, message turns, input history and response-needed events,
and exposes an LLM-ready thread so other processes can hand the whole conversation to SoloLLM.

Start from the repository root:

python .\SoloChat\main.py

Then open:

http://127.0.0.1:9720/

Status endpoints:

- /status
- /api/snapshot

Core conversation endpoints:

- GET /api/conversations
- POST /api/conversations
- GET /api/conversations/{conversation_id}
- GET /api/conversations/{conversation_id}/detail
- PATCH /api/conversations/{conversation_id}
- DELETE /api/conversations/{conversation_id}
- GET /api/conversations/{conversation_id}/messages
- POST /api/conversations/{conversation_id}/messages
- GET /api/conversations/{conversation_id}/llm-thread
- POST /api/conversations/{conversation_id}/queue-response

External-id convenience endpoints:

- GET /api/conversations/by-external-id/{external_id}
- GET /api/conversations/by-external-id/{external_id}/detail
- GET /api/conversations/by-external-id/{external_id}/turns
- GET /api/conversations/by-external-id/{external_id}/llm-thread
- POST /api/conversations/by-external-id/{external_id}/messages

Queue and history endpoints:

- GET /api/events
- POST /api/events
- GET /api/events/next?claimed_by=agent
- POST /api/events/{event_id}/complete
- GET /api/conversations/{conversation_id}/input-history
- PATCH /api/conversations/{conversation_id}/input-history

Storage:

- SQLite database: Data\SoloChat\solochat.db
- Web UI: SoloChat\ui
