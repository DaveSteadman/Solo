# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Runs the SoloGraph SQLite-backed knowledge graph service.

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any


SOLO_GRAPH_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_GRAPH_ROOT.parent
SOLO_ROOT = SOLO_DATA_ROOT.parent
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.config import load_config, resolve_data_path, service_host_port  # noqa: E402
from common_utils.service import parse_service_args, run_http_service  # noqa: E402
from common_utils.sqlite import sqlite_connection  # noqa: E402
from router import EndpointRouter  # noqa: E402
from endpoints import connection_endpoints, csv_endpoints, vocab_endpoints  # noqa: E402
import database  # noqa: E402


UI_DIR = SOLO_GRAPH_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()


class SoloGraphStore:
    def __init__(self, graph_root: Path) -> None:
        self.graph_root = graph_root
        self.db_path = graph_root / "graph.db"
        self.log_dir = graph_root / "logs"
        self.graph_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        database.create_database(self.db_path)

    # -------------------------------------------------------------------------
    # MARK: Status
    # -------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with sqlite_connection(self.db_path) as conn:
            vocab_count = conn.execute("SELECT COUNT(*) FROM vocab").fetchone()[0]
            connection_count = conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
        return {
            "service": "SoloGraph",
            "status": "running",
            "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
            "vocab": vocab_count,
            "connections": connection_count,
            "db": str(self.db_path),
        }

    # -------------------------------------------------------------------------
    # MARK: Vocab
    # -------------------------------------------------------------------------

    def list_vocab(self, q: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with sqlite_connection(self.db_path) as conn:
            return database.list_vocab(conn, q=q, limit=limit, offset=offset)

    def get_vocab(self, vocab_id: int) -> dict[str, Any] | None:
        with sqlite_connection(self.db_path) as conn:
            return database.get_vocab(conn, vocab_id)

    def add_vocab(self, payload: dict[str, Any]) -> dict[str, Any]:
        term = str(payload.get("term") or "").strip()
        with sqlite_connection(self.db_path) as conn:
            return database.add_vocab(conn, term)

    def update_vocab(self, vocab_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        term = str(payload.get("term") or "").strip()
        with sqlite_connection(self.db_path) as conn:
            return database.update_vocab(conn, vocab_id, term)

    # -------------------------------------------------------------------------
    # MARK: Connections
    # -------------------------------------------------------------------------

    def list_connections(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with sqlite_connection(self.db_path) as conn:
            return database.list_connections(conn, limit=limit, offset=offset)

    def add_connection_by_name(self, payload: dict[str, Any]) -> dict[str, Any]:
        start = str(payload.get("start") or "").strip()
        connection = str(payload.get("connection") or "").strip()
        end = str(payload.get("end") or "").strip()
        if not start or not connection or not end:
            raise ValueError("start, connection, and end are required")
        with sqlite_connection(self.db_path) as conn:
            return database.add_connection_by_name(conn, start, connection, end)

    def add_connections_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        items = payload.get("connections") if isinstance(payload.get("connections"), list) else []
        added = 0
        skipped = 0
        with sqlite_connection(self.db_path) as conn:
            for item in items:
                start = str(item.get("start") or "").strip()
                connection = str(item.get("connection") or "").strip()
                end = str(item.get("end") or "").strip()
                if not start or not connection or not end:
                    skipped += 1
                    continue
                database.add_connection_by_name(conn, start, connection, end)
                added += 1
        return {"added": added, "skipped": skipped}

    def delete_connection(self, connection_id: int) -> bool:
        with sqlite_connection(self.db_path) as conn:
            return database.delete_connection(conn, connection_id)

    # -------------------------------------------------------------------------
    # MARK: Search
    # -------------------------------------------------------------------------

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite_connection(self.db_path) as conn:
            return database.search_connections(conn, q, limit=limit)


def build_route_handler(store: SoloGraphStore) -> Any:
    router = EndpointRouter()
    router.store = store

    router.add("GET",    "/api/connections",        connection_endpoints.list_connections)
    router.add("POST",   "/api/connections",        connection_endpoints.add_connection)
    router.add("POST",   "/api/connections/batch",  connection_endpoints.add_connections_batch)
    router.add("DELETE", "/api/connections",        connection_endpoints.delete_connection)

    router.add("GET",    "/api/vocab",              vocab_endpoints.list_vocab)
    router.add("GET",    "/api/vocab/item",         vocab_endpoints.get_vocab)
    router.add("POST",   "/api/vocab",              vocab_endpoints.add_vocab)
    router.add("PATCH",  "/api/vocab",              vocab_endpoints.update_vocab)

    router.add("GET",    "/api/export/connections", csv_endpoints.export_connections)
    router.add("POST",   "/api/import/connections", csv_endpoints.import_connections)

    def handle(handler: Any, method: str, path: str, params: dict, payload: dict) -> bool:
        return router.handle(handler, method, path, params, payload)

    return handle


def main() -> int:

    # Write startup message to console
    print("Starting SoloGraph service...")

    args = parse_service_args("Start the SoloGraph service.")
    config = load_config()
    host, port = service_host_port(config, "solograph", 9745)
    host = args.host or host
    port = int(args.port or port)
    store = SoloGraphStore(resolve_data_path(config, "SoloGraph"))
    if args.command == "status" or args.dry_run:
        print(store.status())
        return 0
    run_http_service(
        label="SoloGraph",
        host=host,
        port=port,
        ui_dir=UI_DIR,
        common_ui_dir=COMMON_UI_DIR,
        status_payload=store.status,
        route_handler=build_route_handler(store),
    )

    # Write shutdown message to console
    print("SoloGraph service stopping...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())