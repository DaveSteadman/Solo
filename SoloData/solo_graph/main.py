# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Runs the SoloGraph SQLite-backed concept graph.

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any


SOLO_GRAPH_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_GRAPH_ROOT.parent
SOLO_ROOT = SOLO_DATA_ROOT.parent
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.config import load_config, resolve_solo_path, service_host_port  # noqa: E402
from common_utils.service import parse_service_args, query_int, query_text, run_http_service  # noqa: E402
from common_utils.sqlite import sqlite_connection  # noqa: E402
from common_utils.web import send_json  # noqa: E402


UI_DIR = SOLO_GRAPH_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()
DEFAULT_PREDICATES = (
    "is_a",
    "part_of",
    "contributed_to",
    "discovered",
    "developed",
    "proposed",
    "invented",
    "studied",
    "applied_to",
    "influenced",
    "precedes",
    "lived_in",
    "wrote",
    "disproved",
    "succeeded",
    "is_type_of",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SoloGraphStore:
    def __init__(self, graph_root: Path) -> None:
        self.graph_root = graph_root
        self.db_path = graph_root / "graph.db"
        self.log_dir = graph_root / "logs"
        self.graph_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self) -> None:
        with sqlite_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vocab (
                    id INTEGER PRIMARY KEY,
                    term TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    kind TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY,
                    start_id INTEGER NOT NULL,
                    predicate TEXT NOT NULL,
                    end_id INTEGER NOT NULL,
                    score REAL NOT NULL DEFAULT 1.0,
                    state TEXT NOT NULL DEFAULT 'active',
                    evidence TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(start_id, predicate, end_id),
                    FOREIGN KEY(start_id) REFERENCES vocab(id) ON DELETE CASCADE,
                    FOREIGN KEY(end_id) REFERENCES vocab(id) ON DELETE CASCADE
                );
                """
            )
            for predicate in DEFAULT_PREDICATES:
                self.get_or_create_vocab(conn, predicate, kind="predicate")

    def status(self) -> dict[str, Any]:
        with sqlite_connection(self.db_path) as conn:
            vocab = conn.execute("SELECT COUNT(*) FROM vocab").fetchone()[0]
            relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        return {"service": "SoloGraph", "status": "ok", "vocab": vocab, "relations": relations, "dataRoot": str(self.graph_root)}

    def snapshot(self) -> dict[str, Any]:
        status = self.status()
        return {
            "service": "SoloGraph",
            "status": "running",
            "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
            "paths": {"graphRoot": str(self.graph_root), "db": str(self.db_path), "logs": str(self.log_dir)},
            "metrics": status,
            "recentConnections": self.list_connections(limit=20),
            "recentVocab": self.list_vocab(limit=20),
        }

    def list_vocab(self, q: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        where = "WHERE term LIKE ?" if q else ""
        params: list[Any] = [f"%{q}%"] if q else []
        params.extend([max(1, min(limit, 250)), max(0, offset)])
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM vocab {where} ORDER BY term LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def add_vocab(self, payload: dict[str, Any]) -> dict[str, Any]:
        term = str(payload.get("term") or "").strip()
        if not term:
            raise ValueError("term is required")
        with sqlite_connection(self.db_path) as conn:
            vocab_id = self.get_or_create_vocab(conn, term, kind=payload.get("kind"), notes=payload.get("notes"))
            row = conn.execute("SELECT * FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
        return dict(row)

    def update_vocab(self, vocab_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        fields = {key: payload[key] for key in ("term", "kind", "notes") if key in payload}
        if not fields:
            return self.get_vocab(vocab_id)
        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(str(value).strip() if key == "term" else value)
        assignments.append("updated_at = ?")
        values.extend([utc_now(), vocab_id])
        with sqlite_connection(self.db_path) as conn:
            conn.execute(f"UPDATE vocab SET {', '.join(assignments)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
        return dict(row) if row else None

    def get_vocab(self, vocab_id: int) -> dict[str, Any] | None:
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
        return dict(row) if row else None

    def delete_vocab(self, vocab_id: int) -> bool:
        with sqlite_connection(self.db_path) as conn:
            cur = conn.execute("DELETE FROM vocab WHERE id = ?", (vocab_id,))
        return cur.rowcount > 0

    def list_connections(self, limit: int = 100, offset: int = 0, state: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE r.state = ?" if state else ""
        params: list[Any] = [state] if state else []
        params.extend([max(1, min(limit, 250)), max(0, offset)])
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT r.id, r.start_id, s.term AS start, r.predicate, r.end_id, e.term AS end,
                       r.score, r.state, r.evidence, r.created_at, r.updated_at
                FROM relations r
                JOIN vocab s ON s.id = r.start_id
                JOIN vocab e ON e.id = r.end_id
                {where}
                ORDER BY r.updated_at DESC, r.id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_connection_by_name(self, payload: dict[str, Any]) -> dict[str, Any]:
        start = str(payload.get("start") or "").strip()
        predicate = str(payload.get("connection") or payload.get("predicate") or "").strip()
        end = str(payload.get("end") or "").strip()
        if not start or not predicate or not end:
            raise ValueError("start, connection and end are required")
        score = float(payload.get("score") or 1.0)
        evidence = str(payload.get("evidence") or "").strip() or None
        now = utc_now()
        with sqlite_connection(self.db_path) as conn:
            start_id = self.get_or_create_vocab(conn, start)
            end_id = self.get_or_create_vocab(conn, end)
            cur = conn.execute(
                """
                INSERT INTO relations(start_id, predicate, end_id, score, state, evidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(start_id, predicate, end_id)
                DO UPDATE SET score = score + excluded.score,
                              evidence = COALESCE(excluded.evidence, evidence),
                              updated_at = excluded.updated_at
                """,
                (start_id, predicate, end_id, score, evidence, now, now),
            )
            row = conn.execute(
                """
                SELECT r.id, r.start_id, s.term AS start, r.predicate, r.end_id, e.term AS end,
                       r.score, r.state, r.evidence, r.created_at, r.updated_at
                FROM relations r
                JOIN vocab s ON s.id = r.start_id
                JOIN vocab e ON e.id = r.end_id
                WHERE r.start_id = ? AND r.predicate = ? AND r.end_id = ?
                """,
                (start_id, predicate, end_id),
            ).fetchone()
        return dict(row)

    def create_connections_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        items = payload.get("connections") if isinstance(payload.get("connections"), list) else []
        results = [self.create_connection_by_name(item) for item in items if isinstance(item, dict)]
        return {"count": len(results), "connections": results}

    def update_connection(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        relation_id = int(payload.get("id") or payload.get("connection_id") or 0)
        fields = {key: payload[key] for key in ("state", "score", "evidence") if key in payload}
        if not relation_id or not fields:
            return None
        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        assignments.append("updated_at = ?")
        values.extend([utc_now(), relation_id])
        with sqlite_connection(self.db_path) as conn:
            conn.execute(f"UPDATE relations SET {', '.join(assignments)} WHERE id = ?", values)
        return next((item for item in self.list_connections(limit=250) if item["id"] == relation_id), None)

    def delete_connection(self, relation_id: int) -> bool:
        with sqlite_connection(self.db_path) as conn:
            cur = conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
        return cur.rowcount > 0

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q:
            return {"query": q, "results": []}
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT r.id, s.term AS start, r.predicate, e.term AS end, r.score, r.state, r.evidence, r.updated_at
                FROM relations r
                JOIN vocab s ON s.id = r.start_id
                JOIN vocab e ON e.id = r.end_id
                WHERE s.term LIKE ? OR e.term LIKE ? OR r.predicate LIKE ? OR r.evidence LIKE ?
                ORDER BY r.score DESC, r.updated_at DESC
                LIMIT ?
                """,
                (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", max(1, min(limit, 200))),
            ).fetchall()
        return {"query": q, "results": [dict(row) for row in rows]}

    def expand_by_term(self, term: str, depth: int = 1, limit: int = 50) -> dict[str, Any]:
        seed = str(term or "").strip()
        if not seed:
            return {"nodes": [], "edges": []}
        edges = self.search(seed, limit=limit)["results"]
        nodes = sorted({edge["start"] for edge in edges} | {edge["end"] for edge in edges})
        return {"term": seed, "depth": max(1, min(depth, 3)), "nodes": nodes, "edges": edges}

    @staticmethod
    def get_or_create_vocab(conn: Any, term: str, kind: Any = None, notes: Any = None) -> int:
        clean = str(term or "").strip()
        now = utc_now()
        row = conn.execute("SELECT id FROM vocab WHERE term = ? COLLATE NOCASE", (clean,)).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO vocab(term, kind, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (clean, kind, notes, now, now),
        )
        return int(cur.lastrowid)


def build_route_handler(store: SoloGraphStore):
    def handle(handler: Any, method: str, path: str, params: dict[str, list[str]], payload: dict[str, Any]) -> bool:
        try:
            if method == "GET" and path == "/api/snapshot":
                send_json(handler, store.snapshot())
                return True
            if method == "GET" and path == "/api/vocab":
                send_json(handler, {"vocab": store.list_vocab(q=query_text(params, "q"), limit=query_int(params, "limit", 100), offset=query_int(params, "offset", 0))})
                return True
            if method == "POST" and path == "/api/vocab":
                send_json(handler, store.add_vocab(payload), status=HTTPStatus.CREATED)
                return True
            if path.startswith("/api/vocab/"):
                vocab_id = int(path.removeprefix("/api/vocab/").split("/", 1)[0])
                if method == "GET":
                    item = store.get_vocab(vocab_id)
                    send_json(handler, item or {"error": "Term not found"}, status=HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
                    return True
                if method == "PATCH":
                    item = store.update_vocab(vocab_id, payload)
                    send_json(handler, item or {"error": "Term not found"}, status=HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
                    return True
                if method == "DELETE":
                    ok = store.delete_vocab(vocab_id)
                    send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                    return True
            if method == "GET" and path == "/api/connections":
                send_json(handler, {"connections": store.list_connections(limit=query_int(params, "limit", 100), offset=query_int(params, "offset", 0), state=query_text(params, "state") or None)})
                return True
            if method == "POST" and path == "/api/connections/by-name":
                send_json(handler, store.create_connection_by_name(payload), status=HTTPStatus.CREATED)
                return True
            if method == "POST" and path == "/api/connections/by-name/batch":
                send_json(handler, store.create_connections_batch(payload), status=HTTPStatus.CREATED)
                return True
            if method == "PATCH" and path == "/api/connections":
                item = store.update_connection(payload)
                send_json(handler, item or {"error": "Connection not found"}, status=HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
                return True
            if method == "DELETE" and path == "/api/connections":
                ok = store.delete_connection(int(payload.get("id") or payload.get("connection_id") or query_text(params, "id") or 0))
                send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                return True
            if method == "GET" and path == "/api/search":
                send_json(handler, store.search(query_text(params, "q"), limit=query_int(params, "limit", 20)))
                return True
            if method == "GET" and path == "/api/expand-by-term":
                send_json(handler, store.expand_by_term(query_text(params, "term"), depth=query_int(params, "depth", 1), limit=query_int(params, "limit", 50)))
                return True
        except ValueError as exc:
            handler.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return True
        return False

    return handle


def main() -> int:
    args = parse_service_args("Start the SoloGraph service.")
    config = load_config()
    host, port = service_host_port(config, "solograph", 9745)
    host = args.host or host
    port = int(args.port or port)
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    store = SoloGraphStore(resolve_solo_path(paths.get("soloDataGraphRoot"), "./Data/SoloData/Graph"))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
