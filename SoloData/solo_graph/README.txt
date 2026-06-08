SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Runs the SoloGraph service.

Run:

python .\SoloData\solo_graph\main.py

Endpoints:

GET  /status
GET  /api/snapshot
GET  /api/vocab
POST /api/vocab
GET  /api/connections
POST /api/connections/by-name
POST /api/connections/by-name/batch
GET  /api/search?q=term
