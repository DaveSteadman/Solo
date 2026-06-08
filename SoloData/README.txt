SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Documents the SoloData gateway service.

SoloData is a launchable local gateway that exposes a JSON-specified web UI, starts child data services,
and provides MCP search/edit tools for SoloAgent.

Run from the repository root:

python .\SoloData\main.py

Then open:

http://127.0.0.1:9740/

Useful endpoints:

GET /status
GET /api/snapshot
GET /api/search?q=term&domain=all
POST /api/full-text
MCP /mcp

The UI is driven by:

SoloData/ui/page.json
