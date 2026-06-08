SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Runs the SoloData gateway web UI and MCP server.

SoloDataGateway owns the SoloData top-level page and starts child data services, currently SoloLibrary.
SoloAgent connects to the gateway through Streamable HTTP MCP at:

http://127.0.0.1:9740/mcp

Run from the repository root:

python .\SoloData\solo_data_gateway\main.py

Useful endpoints:

GET  /status
GET  /api/snapshot
GET  /api/search?q=term&domain=all
POST /api/search
POST /api/full-text

The web UI remains JSON-driven by:

SoloData/ui/page.json
