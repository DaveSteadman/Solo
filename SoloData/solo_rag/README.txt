SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Runs the SoloRAG service.

Run:

python .\SoloData\solo_rag\main.py

Endpoints:

GET  /status
GET  /api/snapshot
GET  /databases
POST /databases
GET  /chunks?db=default
POST /chunks?db=default
GET  /search?q=term&db=default
GET  /search/all?q=term
