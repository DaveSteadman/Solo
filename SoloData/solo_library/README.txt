SPDX-License-Identifier: MIT
Copyright (c) 2026 Solo contributors

Purpose:
Documents the SoloLibrary book database service.

SoloLibrary is a launchable local service for book catalogs backed by SQLite databases.
Book bodies are stored compressed and indexed through SQLite FTS5.

Run from the repository root:

python .\SoloData\solo_library\main.py

Then open:

http://127.0.0.1:9741/

Useful endpoints:

GET /status
GET /api/snapshot
GET /api/catalogs
GET /api/books
GET /api/search?q=term
POST /api/books
POST /api/search

The UI is driven by:

SoloData/solo_library/ui/page.json

Database files live under:

Data/SoloData/Library/catalogs
