# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Runs the SoloRAG SQLite-backed chunk store.

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any


SOLO_RAG_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_RAG_ROOT.parent
SOLO_ROOT = SOLO_DATA_ROOT.parent
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.compression import compress_text, decompress_text  # noqa: E402
from common_utils.config import load_config, resolve_solo_path, service_host_port  # noqa: E402
from common_utils.service import parse_service_args, query_int, query_text, run_http_service  # noqa: E402
from common_utils.sqlite import compute_word_count, fts_build_query, sqlite_connection  # noqa: E402
from common_utils.web import send_json  # noqa: E402


UI_DIR = SOLO_RAG_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def blank_to_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


class SoloRAGStore:
    def __init__(self, rag_root: Path) -> None:
        self.rag_root = rag_root
        self.databases_root = rag_root / "databases"
        self.log_dir = rag_root / "logs"
        self.rag_root.mkdir(parents=True, exist_ok=True)
        self.databases_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.init_db("default")

    def db_path(self, name: str) -> Path:
        db_name = self.normalize_db(name)
        return self.databases_root / f"{db_name}.db"

    def normalize_db(self, name: str | None) -> str:
        db_name = str(name or "default").strip().lower() or "default"
        if not DB_NAME_RE.fullmatch(db_name):
            raise ValueError(f"Invalid database name: {name!r}")
        return db_name

    def init_db(self, db: str) -> None:
        with sqlite_connection(self.db_path(db)) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT,
                    tags TEXT,
                    metadata TEXT,
                    content TEXT,
                    word_count INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(title, source, tags, content, tokenize='porter');
                """
            )

    def list_databases(self) -> list[dict[str, Any]]:
        self.init_db("default")
        databases = []
        for path in sorted(self.databases_root.glob("*.db")):
            name = path.stem
            status = self.status(name)
            databases.append({"name": name, "path": str(path), "chunks": status["chunks"], "sizeBytes": path.stat().st_size})
        return databases

    def status(self, db: str | None = None) -> dict[str, Any]:
        if db:
            db_name = self.normalize_db(db)
            self.init_db(db_name)
            with sqlite_connection(self.db_path(db_name)) as conn:
                chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            return {"service": "SoloRAG", "status": "ok", "database": db_name, "chunks": chunks, "dataRoot": str(self.rag_root)}
        databases = self.list_databases()
        return {
            "service": "SoloRAG",
            "status": "ok",
            "databases": len(databases),
            "chunks": sum(item["chunks"] for item in databases),
            "dataRoot": str(self.rag_root),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "service": "SoloRAG",
            "status": "running",
            "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
            "paths": {"ragRoot": str(self.rag_root), "databases": str(self.databases_root), "logs": str(self.log_dir)},
            "metrics": self.status(),
            "databases": self.list_databases(),
            "recentChunks": self.list_chunks(limit=20),
        }

    def create_database(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = self.normalize_db(str(payload.get("name") or ""))
        self.init_db(name)
        return {"name": name, "path": str(self.db_path(name))}

    def delete_database(self, name: str) -> bool:
        db_name = self.normalize_db(name)
        if db_name == "default":
            raise ValueError("default database cannot be deleted")
        path = self.db_path(db_name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_chunks(self, limit: int = 100, offset: int = 0, db: str = "default") -> list[dict[str, Any]]:
        db_name = self.normalize_db(db)
        self.init_db(db_name)
        with sqlite_connection(self.db_path(db_name)) as conn:
            rows = conn.execute(
                """
                SELECT id, title, source, tags, metadata, word_count, created_at, updated_at
                FROM chunks
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (max(1, min(limit, 250)), max(0, offset)),
            ).fetchall()
        return [self._row_to_chunk(row, db_name) for row in rows]

    def get_chunk(self, chunk_id: int, db: str = "default") -> dict[str, Any] | None:
        db_name = self.normalize_db(db)
        self.init_db(db_name)
        with sqlite_connection(self.db_path(db_name)) as conn:
            row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row, db_name, include_content=True) if row else None

    def add_chunk(self, payload: dict[str, Any], db: str = "default") -> dict[str, Any]:
        db_name = self.normalize_db(db)
        self.init_db(db_name)
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("content") or "")
        if not title or not content:
            raise ValueError("title and content are required")
        now = utc_now()
        tags = tags_to_text(payload.get("tags"))
        metadata = metadata_to_text(payload.get("metadata"))
        with sqlite_connection(self.db_path(db_name)) as conn:
            cur = conn.execute(
                """
                INSERT INTO chunks (title, source, tags, metadata, content, word_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    blank_to_none(payload.get("source")),
                    tags,
                    metadata,
                    compress_text(content),
                    compute_word_count(content),
                    now,
                    now,
                ),
            )
            chunk_id = int(cur.lastrowid)
            self._fts_insert(conn, chunk_id, title, str(payload.get("source") or ""), tags, content)
            row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row, db_name, include_content=True)

    def update_chunk(self, chunk_id: int, payload: dict[str, Any], db: str = "default") -> dict[str, Any] | None:
        db_name = self.normalize_db(db)
        self.init_db(db_name)
        allowed = ("title", "source", "tags", "metadata", "content")
        fields = {key: payload[key] for key in allowed if key in payload}
        if not fields:
            return self.get_chunk(chunk_id, db_name)
        with sqlite_connection(self.db_path(db_name)) as conn:
            current = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if current is None:
                return None
            self._fts_delete(conn, chunk_id, current["title"] or "", current["source"] or "", current["tags"] or "", decompress_text(current["content"]) or "")
            assignments: list[str] = []
            values: list[Any] = []
            for key in allowed:
                if key not in fields:
                    continue
                assignments.append(f"{key} = ?")
                if key == "content":
                    content = str(fields[key] or "")
                    values.append(compress_text(content))
                    assignments.append("word_count = ?")
                    values.append(compute_word_count(content))
                elif key == "tags":
                    values.append(tags_to_text(fields[key]))
                elif key == "metadata":
                    values.append(metadata_to_text(fields[key]))
                elif key == "title":
                    title = str(fields[key] or "").strip()
                    if not title:
                        raise ValueError("title is required")
                    values.append(title)
                else:
                    values.append(blank_to_none(fields[key]))
            assignments.append("updated_at = ?")
            values.extend([utc_now(), chunk_id])
            conn.execute(f"UPDATE chunks SET {', '.join(assignments)} WHERE id = ?", values)
            updated = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            self._fts_insert(conn, chunk_id, updated["title"] or "", updated["source"] or "", updated["tags"] or "", decompress_text(updated["content"]) or "")
        return self._row_to_chunk(updated, db_name, include_content=True)

    def delete_chunk(self, chunk_id: int, db: str = "default") -> bool:
        db_name = self.normalize_db(db)
        with sqlite_connection(self.db_path(db_name)) as conn:
            row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row is None:
                return False
            self._fts_delete(conn, chunk_id, row["title"] or "", row["source"] or "", row["tags"] or "", decompress_text(row["content"]) or "")
            conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
        return True

    def search(self, query: str, limit: int = 20, db: str = "default", source: str | None = None, tags: str | None = None) -> dict[str, Any]:
        db_name = self.normalize_db(db)
        self.init_db(db_name)
        query = query.strip()
        limit = max(1, min(limit, 200))
        if not query:
            return {"query": query, "database": db_name, "results": []}
        fts_query = fts_build_query(query)
        where_extra = []
        params: list[Any] = [fts_query]
        if source:
            where_extra.append("c.source = ?")
            params.append(source)
        if tags:
            where_extra.append("c.tags LIKE ?")
            params.append(f"%{tags}%")
        extra_sql = (" AND " + " AND ".join(where_extra)) if where_extra else ""
        params.append(limit)
        with sqlite_connection(self.db_path(db_name)) as conn:
            rows = conn.execute(
                f"""
                SELECT c.id, c.title, c.source, c.tags, c.metadata, c.word_count, c.created_at, c.updated_at,
                       snippet(chunks_fts, 3, '[', ']', '...', 28) AS snippet
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ? {extra_sql}
                ORDER BY rank
                LIMIT ?
                """,
                params,
            ).fetchall()
        return {"query": query, "database": db_name, "results": [self._row_to_chunk(row, db_name) | {"snippet": row["snippet"]} for row in rows]}

    def search_all(self, query: str, limit: int = 20) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for database in self.list_databases():
            results.extend(self.search(query, limit=limit, db=database["name"])["results"])
        return {"query": query, "results": results[: max(1, min(limit, 200))]}

    @staticmethod
    def _row_to_chunk(row: Any, db: str, include_content: bool = False) -> dict[str, Any]:
        data = {key: row[key] for key in row.keys() if key != "content"}
        data["db"] = db
        data["tags"] = tags_from_text(data.get("tags"))
        data["metadata"] = metadata_from_text(data.get("metadata"))
        if include_content:
            data["content"] = decompress_text(row["content"])
        return data

    @staticmethod
    def _fts_insert(conn: Any, chunk_id: int, title: str, source: str, tags: str, content: str) -> None:
        conn.execute("INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)", (chunk_id, title or "", source or "", tags or "", content or ""))

    @staticmethod
    def _fts_delete(conn: Any, chunk_id: int, title: str, source: str, tags: str, content: str) -> None:
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))


def tags_to_text(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def tags_from_text(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def metadata_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {}, sort_keys=True)


def metadata_from_text(value: Any) -> Any:
    try:
        return json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}


def build_route_handler(store: SoloRAGStore):
    def handle(handler: Any, method: str, path: str, params: dict[str, list[str]], payload: dict[str, Any]) -> bool:
        try:
            db = query_text(params, "db", "default")
            if method == "GET" and path == "/api/snapshot":
                send_json(handler, store.snapshot())
                return True
            if method == "GET" and path == "/databases":
                send_json(handler, {"databases": store.list_databases()})
                return True
            if method == "POST" and path == "/databases":
                send_json(handler, store.create_database(payload), status=HTTPStatus.CREATED)
                return True
            if method == "DELETE" and path.startswith("/databases/"):
                ok = store.delete_database(path.removeprefix("/databases/"))
                send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                return True
            if method == "GET" and path.startswith("/databases/") and path.endswith("/info"):
                name = path.removeprefix("/databases/").removesuffix("/info")
                send_json(handler, store.status(name))
                return True
            if method == "GET" and path == "/chunks":
                send_json(handler, {"chunks": store.list_chunks(limit=query_int(params, "limit", 100), offset=query_int(params, "offset", 0), db=db)})
                return True
            if method == "POST" and path == "/chunks":
                send_json(handler, store.add_chunk(payload, db=db), status=HTTPStatus.CREATED)
                return True
            if path.startswith("/chunks/"):
                chunk_id = int(path.removeprefix("/chunks/"))
                if method == "GET":
                    chunk = store.get_chunk(chunk_id, db=db)
                    send_json(handler, chunk or {"error": "Chunk not found"}, status=HTTPStatus.OK if chunk else HTTPStatus.NOT_FOUND)
                    return True
                if method == "PATCH":
                    chunk = store.update_chunk(chunk_id, payload, db=db)
                    send_json(handler, chunk or {"error": "Chunk not found"}, status=HTTPStatus.OK if chunk else HTTPStatus.NOT_FOUND)
                    return True
                if method == "DELETE":
                    ok = store.delete_chunk(chunk_id, db=db)
                    send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                    return True
            if method == "GET" and path == "/search":
                send_json(
                    handler,
                    store.search(
                        query_text(params, "q"),
                        limit=query_int(params, "limit", 20),
                        db=db,
                        source=query_text(params, "source") or None,
                        tags=query_text(params, "tags") or None,
                    ),
                )
                return True
            if method == "GET" and path == "/search/all":
                send_json(handler, store.search_all(query_text(params, "q"), limit=query_int(params, "limit", 20)))
                return True
            if method == "GET" and path == "/api/search":
                send_json(handler, store.search_all(query_text(params, "q"), limit=query_int(params, "limit", 20)))
                return True
        except ValueError as exc:
            handler.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return True
        return False

    return handle


def main() -> int:
    args = parse_service_args("Start the SoloRAG service.")
    config = load_config()
    host, port = service_host_port(config, "solorag", 9744)
    host = args.host or host
    port = int(args.port or port)
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    store = SoloRAGStore(resolve_solo_path(paths.get("soloDataRAGRoot"), "./Data/SoloData/RAG"))
    if args.command == "status" or args.dry_run:
        print(store.status())
        return 0
    run_http_service(
        label="SoloRAG",
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
