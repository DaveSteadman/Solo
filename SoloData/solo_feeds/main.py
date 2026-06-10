# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Runs the SoloFeeds SQLite-backed feed and article store.

from __future__ import annotations

import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any


SOLO_FEEDS_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_FEEDS_ROOT.parent
SOLO_ROOT = SOLO_DATA_ROOT.parent
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.compression import compress_text, decompress_text  # noqa: E402
from common_utils.config import load_config, resolve_data_path, service_host_port  # noqa: E402
from common_utils.service import parse_service_args, query_int, query_text, run_http_service  # noqa: E402
from common_utils.sqlite import fts_build_query, sqlite_connection  # noqa: E402
from common_utils.web import send_json  # noqa: E402


UI_DIR = SOLO_FEEDS_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def blank_to_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


class SoloFeedsStore:
    def __init__(self, feeds_root: Path) -> None:
        self.feeds_root = feeds_root
        self.db_path = feeds_root / "feeds.db"
        self.log_dir = feeds_root / "logs"
        self.feeds_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self) -> None:
        with sqlite_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS domains (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feeds (
                    id INTEGER PRIMARY KEY,
                    domain TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY,
                    feed_id INTEGER,
                    domain TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    url TEXT,
                    author TEXT,
                    published_at TEXT,
                    summary TEXT,
                    content TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(feed_id) REFERENCES feeds(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS domain_settings (
                    domain TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'days_previous',
                    days INTEGER,
                    start_date TEXT,
                    end_date TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(domain) REFERENCES domains(name) ON DELETE CASCADE
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
                USING fts5(title, summary, content, tokenize='porter');
                """
            )
            self._ensure_column(conn, "feeds", "update_minutes INTEGER NOT NULL DEFAULT 60")
            self._ensure_column(conn, "feeds", "last_run_at TEXT")
            self._ensure_column(conn, "feeds", "last_duration_s REAL")
            self._ensure_column(conn, "feeds", "last_status TEXT")
            self._ensure_column(conn, "feeds", "last_error TEXT")
            self._ensure_column(conn, "feeds", "content_status TEXT")
            self._ensure_column(conn, "feeds", "last_new_entries INTEGER")
            self._ensure_column(conn, "feeds", "next_run_at TEXT")
            self._sync_domains(conn)

    def status(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "service": "SoloFeeds",
            "status": "ok",
            "domains": snap["metrics"]["domains"],
            "feeds": snap["metrics"]["feeds"],
            "entries": snap["metrics"]["entries"],
            "dataRoot": str(self.feeds_root),
        }

    def snapshot(self) -> dict[str, Any]:
        with sqlite_connection(self.db_path) as conn:
            domains = self._list_domains_with_conn(conn)
            feeds = self._list_feeds_with_conn(conn)
            entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            recent = conn.execute(
                """
                SELECT e.id, e.feed_id, e.domain, e.title, e.url, e.author, e.published_at, e.summary,
                       e.created_at, e.updated_at, f.title AS feed_title
                FROM entries e
                LEFT JOIN feeds f ON f.id = e.feed_id
                ORDER BY COALESCE(NULLIF(e.published_at, ''), e.updated_at) DESC, e.id DESC
                LIMIT 20
                """
            ).fetchall()
        return {
            "service": "SoloFeeds",
            "status": "running",
            "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
            "paths": {"feedsRoot": str(self.feeds_root), "db": str(self.db_path), "logs": str(self.log_dir)},
            "metrics": {"domains": len(domains), "feeds": len(feeds), "entries": entries},
            "domains": domains,
            "feeds": feeds,
            "recentEntries": [self._row_to_entry(row) for row in recent],
        }

    def list_domains(self) -> list[dict[str, Any]]:
        with sqlite_connection(self.db_path) as conn:
            return self._list_domains_with_conn(conn)

    def get_domain_snapshot(self, domain: str, entry_limit: int = 100) -> dict[str, Any] | None:
        normalized = self._normalize_domain_name(domain)
        entry_limit = max(1, min(entry_limit, 250))
        with sqlite_connection(self.db_path) as conn:
            domain_names = self._domain_names_with_conn(conn)
            if normalized not in domain_names:
                return None
            feeds = self._list_feeds_with_conn(conn, domain=normalized)
            entries = self._list_entries_with_conn(conn, limit=entry_limit, offset=0, domain=normalized)
            settings = self._domain_settings_with_conn(conn, normalized)
            total_entries = conn.execute("SELECT COUNT(*) FROM entries WHERE domain = ?", (normalized,)).fetchone()[0]
        return {
            "domain": normalized,
            "settings": settings,
            "metrics": {
                "feeds": len(feeds),
                "entries": total_entries,
            },
            "feeds": feeds,
            "entries": entries,
        }

    def create_domain(self, name: str) -> dict[str, Any]:
        domain = self._normalize_domain_name(name)
        now = utc_now()
        with sqlite_connection(self.db_path) as conn:
            existing = conn.execute("SELECT name FROM domains WHERE name = ?", (domain,)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO domains (name, created_at, updated_at) VALUES (?, ?, ?)",
                    (domain, now, now),
                )
        return {"domain": domain}

    def delete_domain(self, name: str) -> bool:
        domain = self._normalize_domain_name(name)
        with sqlite_connection(self.db_path) as conn:
            existing = domain in self._domain_names_with_conn(conn)
            conn.execute("DELETE FROM entries WHERE domain = ?", (domain,))
            conn.execute("DELETE FROM feeds WHERE domain = ?", (domain,))
            conn.execute("DELETE FROM domains WHERE name = ?", (domain,))
        return existing

    def list_feeds(self) -> list[dict[str, Any]]:
        with sqlite_connection(self.db_path) as conn:
            return self._list_feeds_with_conn(conn)

    def add_feed(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or payload.get("name") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not title or not url:
            raise ValueError("title and url are required")
        domain = self._normalize_domain_name(payload.get("domain") or "default")
        update_minutes = self._coerce_update_minutes(payload.get("updateMinutes") or payload.get("update_minutes") or payload.get("update_rate") or 60)
        now = utc_now()
        with sqlite_connection(self.db_path) as conn:
            self._ensure_domain(conn, domain)
            cur = conn.execute(
                """
                INSERT INTO feeds (
                    domain, title, url, notes, update_minutes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (domain, title, url, blank_to_none(payload.get("notes")), update_minutes, now, now),
            )
            row = conn.execute("SELECT * FROM feeds WHERE id = ?", (int(cur.lastrowid),)).fetchone()
        return self._row_to_feed(row)

    def delete_feed(self, feed_id: int) -> bool:
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute("SELECT id FROM feeds WHERE id = ?", (feed_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        return True

    def update_feed_rate(self, feed_id: int, update_minutes: Any) -> dict[str, Any] | None:
        minutes = self._coerce_update_minutes(update_minutes)
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE feeds SET update_minutes = ?, updated_at = ? WHERE id = ?",
                (minutes, utc_now(), feed_id),
            )
            updated = conn.execute(
                "SELECT f.*, COUNT(e.id) AS entry_count FROM feeds f LEFT JOIN entries e ON e.feed_id = f.id WHERE f.id = ? GROUP BY f.id",
                (feed_id,),
            ).fetchone()
        return self._row_to_feed(updated)

    def delete_entries_for_feed(self, feed_id: int) -> int:
        with sqlite_connection(self.db_path) as conn:
            ids = [int(row[0]) for row in conn.execute("SELECT id FROM entries WHERE feed_id = ?", (feed_id,)).fetchall()]
            if not ids:
                return 0
            conn.executemany("DELETE FROM entries_fts WHERE rowid = ?", [(entry_id,) for entry_id in ids])
            conn.execute("DELETE FROM entries WHERE feed_id = ?", (feed_id,))
        return len(ids)

    def update_domain_settings(self, domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_domain_name(domain)
        mode = str(payload.get("mode") or "days_previous").strip() or "days_previous"
        if mode not in {"none", "days_previous", "calendar_period"}:
            raise ValueError("mode must be one of none, days_previous, calendar_period")
        days = None
        if mode == "days_previous":
            days_value = payload.get("days")
            if days_value in (None, ""):
                raise ValueError("days is required for days_previous mode")
            try:
                days = int(days_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("days must be an integer") from exc
            if days <= 0:
                raise ValueError("days must be greater than 0")
        start_date = blank_to_none(payload.get("startDate") or payload.get("start_date"))
        end_date = blank_to_none(payload.get("endDate") or payload.get("end_date"))
        if mode == "calendar_period" and (not start_date or not end_date):
            raise ValueError("startDate and endDate are required for calendar_period mode")
        with sqlite_connection(self.db_path) as conn:
            if normalized not in self._domain_names_with_conn(conn):
                raise ValueError("domain not found")
            self._ensure_domain(conn, normalized)
            conn.execute(
                """
                INSERT INTO domain_settings (domain, mode, days, start_date, end_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    mode = excluded.mode,
                    days = excluded.days,
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    updated_at = excluded.updated_at
                """,
                (normalized, mode, days, start_date, end_date, utc_now()),
            )
            return self._domain_settings_with_conn(conn, normalized)

    def list_entries(self, limit: int = 100, offset: int = 0, domain: str | None = None) -> list[dict[str, Any]]:
        with sqlite_connection(self.db_path) as conn:
            return self._list_entries_with_conn(conn, limit=limit, offset=offset, domain=domain)

    def _list_entries_with_conn(self, conn: Any, limit: int = 100, offset: int = 0, domain: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 250))
        offset = max(0, offset)
        where = "WHERE e.domain = ?" if domain else ""
        params: list[Any] = [domain] if domain else []
        params.extend([limit, offset])
        rows = conn.execute(
            f"""
            SELECT e.id, e.feed_id, e.domain, e.title, e.url, e.author, e.published_at, e.summary,
                   e.created_at, e.updated_at, f.title AS feed_title
            FROM entries e
            LEFT JOIN feeds f ON f.id = e.feed_id
            {where}
            ORDER BY COALESCE(NULLIF(e.published_at, ''), e.updated_at) DESC, e.id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_entry(row, include_content=True) if row else None

    def add_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        content = str(payload.get("content") or "")
        summary = blank_to_none(payload.get("summary"))
        domain = self._normalize_domain_name(payload.get("domain") or "default")
        now = utc_now()
        with sqlite_connection(self.db_path) as conn:
            self._ensure_domain(conn, domain)
            cur = conn.execute(
                """
                INSERT INTO entries (
                    feed_id, domain, title, url, author, published_at, summary, content, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("feed_id"),
                    domain,
                    title,
                    blank_to_none(payload.get("url")),
                    blank_to_none(payload.get("author")),
                    blank_to_none(payload.get("published_at")),
                    summary,
                    compress_text(content),
                    now,
                    now,
                ),
            )
            entry_id = int(cur.lastrowid)
            self._fts_insert(conn, entry_id, title, summary or "", content)
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_entry(row, include_content=True)

    def update_entry(self, entry_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        allowed = ("feed_id", "domain", "title", "url", "author", "published_at", "summary", "content")
        fields = {key: payload[key] for key in allowed if key in payload}
        if not fields:
            return self.get_entry(entry_id)
        with sqlite_connection(self.db_path) as conn:
            current = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if current is None:
                return None
            self._fts_delete(conn, entry_id, current["title"] or "", current["summary"] or "", decompress_text(current["content"]) or "")
            assignments: list[str] = []
            values: list[Any] = []
            for key in allowed:
                if key not in fields:
                    continue
                assignments.append(f"{key} = ?")
                if key == "content":
                    values.append(compress_text(str(fields[key] or "")))
                elif key == "title":
                    value = str(fields[key] or "").strip()
                    if not value:
                        raise ValueError("title is required")
                    values.append(value)
                else:
                    values.append(blank_to_none(fields[key]))
            assignments.append("updated_at = ?")
            values.extend([utc_now(), entry_id])
            conn.execute(f"UPDATE entries SET {', '.join(assignments)} WHERE id = ?", values)
            updated = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            self._fts_insert(conn, entry_id, updated["title"] or "", updated["summary"] or "", decompress_text(updated["content"]) or "")
        return self._row_to_entry(updated, include_content=True)

    def delete_entry(self, entry_id: int) -> bool:
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                return False
            self._fts_delete(conn, entry_id, row["title"] or "", row["summary"] or "", decompress_text(row["content"]) or "")
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return True

    def search(self, query: str, limit: int = 20, domain: str | None = None) -> dict[str, Any]:
        return self.search_filtered(query, limit=limit, domain=domain)

    def search_filtered(
        self,
        query: str,
        *,
        limit: int = 20,
        domain: str | None = None,
        since: str | None = None,
        until: str | None = None,
        no_older_than_days: float | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        limit = max(1, min(limit, 200))
        if not query:
            return {
                "query": query,
                "domain": domain,
                "since": since,
                "until": until,
                "noOlderThanDays": no_older_than_days,
                "results": [],
            }
        fts_query = fts_build_query(query)
        if not fts_query:
            return {
                "query": query,
                "domain": domain,
                "since": since,
                "until": until,
                "noOlderThanDays": no_older_than_days,
                "results": [],
            }

        normalized_since = self._normalize_date_value(since, end_of_day=False)
        normalized_until = self._normalize_date_value(until, end_of_day=True)
        if no_older_than_days is not None and no_older_than_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=float(no_older_than_days))
            cutoff_text = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if not normalized_since or cutoff_text > normalized_since:
                normalized_since = cutoff_text

        filters: list[str] = []
        params: list[Any] = [fts_query]
        if domain:
            filters.append("e.domain = ?")
            params.append(domain)
        if normalized_since:
            filters.append("COALESCE(NULLIF(e.published_at, ''), e.updated_at) >= ?")
            params.append(normalized_since)
        if normalized_until:
            filters.append("COALESCE(NULLIF(e.published_at, ''), e.updated_at) <= ?")
            params.append(normalized_until)
        params.append(limit)
        where_clause = ""
        if filters:
            where_clause = " AND " + " AND ".join(filters)
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT e.id, e.feed_id, e.domain, e.title, e.url, e.author, e.published_at, e.summary,
                      e.created_at, e.updated_at, f.title AS feed_title,
                       snippet(entries_fts, 2, '[', ']', '...', 28) AS snippet
                FROM entries_fts
                JOIN entries e ON e.id = entries_fts.rowid
                  LEFT JOIN feeds f ON f.id = e.feed_id
                WHERE entries_fts MATCH ? {where_clause}
                ORDER BY rank, COALESCE(NULLIF(e.published_at, ''), e.updated_at) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return {
            "query": query,
            "domain": domain,
            "since": normalized_since,
            "until": normalized_until,
            "noOlderThanDays": no_older_than_days,
            "results": [self._row_to_entry(row) | {"snippet": row["snippet"]} for row in rows],
        }

    def _row_to_entry(self, row: Any, include_content: bool = False) -> dict[str, Any]:
        data = {key: row[key] for key in row.keys() if key != "content"}
        if include_content:
            data["content"] = decompress_text(row["content"])
        return data

    def _row_to_feed(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        next_run_at = data.get("next_run_at") or self._derive_next_run(data.get("last_run_at"), data.get("update_minutes"))
        return {
            "id": data.get("id"),
            "domain": data.get("domain"),
            "title": data.get("title"),
            "url": data.get("url"),
            "notes": data.get("notes"),
            "updateMinutes": data.get("update_minutes") or 60,
            "lastRunAt": data.get("last_run_at"),
            "lastDurationSec": data.get("last_duration_s"),
            "lastStatus": data.get("last_status"),
            "lastError": data.get("last_error"),
            "contentStatus": data.get("content_status"),
            "lastNewEntries": data.get("last_new_entries"),
            "nextRunAt": next_run_at,
            "entryCount": data.get("entry_count") or 0,
        }

    def _list_domains_with_conn(self, conn: Any) -> list[dict[str, Any]]:
        domains = []
        for domain in sorted(self._domain_names_with_conn(conn), key=str.lower):
            feed_count = conn.execute("SELECT COUNT(*) FROM feeds WHERE domain = ?", (domain,)).fetchone()[0]
            entry_count = conn.execute("SELECT COUNT(*) FROM entries WHERE domain = ?", (domain,)).fetchone()[0]
            domains.append({
                "domain": domain,
                "feedCount": feed_count,
                "entryCount": entry_count,
            })
        return domains

    def _list_feeds_with_conn(self, conn: Any, domain: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE f.domain = ?" if domain else ""
        params: tuple[Any, ...] = (domain,) if domain else ()
        rows = conn.execute(
            f"""
            SELECT f.*, COUNT(e.id) AS entry_count
            FROM feeds f
            LEFT JOIN entries e ON e.feed_id = f.id
            {where}
            GROUP BY f.id
            ORDER BY f.domain, f.title
            """,
            params,
        ).fetchall()
        return [self._row_to_feed(row) for row in rows]

    def _domain_settings_with_conn(self, conn: Any, domain: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT domain, mode, days, start_date, end_date, updated_at FROM domain_settings WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row is None:
            return {
                "domain": domain,
                "mode": "days_previous",
                "days": 30,
                "startDate": None,
                "endDate": None,
                "updatedAt": None,
            }
        return {
            "domain": row["domain"],
            "mode": row["mode"],
            "days": row["days"],
            "startDate": row["start_date"],
            "endDate": row["end_date"],
            "updatedAt": row["updated_at"],
        }

    def _domain_names_with_conn(self, conn: Any) -> set[str]:
        names = {str(row[0]) for row in conn.execute("SELECT name FROM domains").fetchall() if row[0]}
        names.update(str(row[0]) for row in conn.execute("SELECT DISTINCT domain FROM feeds WHERE domain IS NOT NULL AND domain <> ''").fetchall() if row[0])
        names.update(str(row[0]) for row in conn.execute("SELECT DISTINCT domain FROM entries WHERE domain IS NOT NULL AND domain <> ''").fetchall() if row[0])
        return names

    def _ensure_domain(self, conn: Any, domain: str) -> None:
        now = utc_now()
        conn.execute(
            "INSERT OR IGNORE INTO domains (name, created_at, updated_at) VALUES (?, ?, ?)",
            (domain, now, now),
        )
        conn.execute("UPDATE domains SET updated_at = ? WHERE name = ?", (now, domain))

    def _sync_domains(self, conn: Any) -> None:
        for domain in self._domain_names_with_conn(conn):
            self._ensure_domain(conn, domain)

    @staticmethod
    def _ensure_column(conn: Any, table: str, definition: str) -> None:
        column = definition.split()[0]
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    @staticmethod
    def _normalize_domain_name(value: Any) -> str:
        domain = str(value or "").strip()
        if not domain:
            raise ValueError("domain is required")
        return domain

    @staticmethod
    def _coerce_update_minutes(value: Any) -> int:
        try:
            minutes = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("updateMinutes must be an integer") from exc
        if minutes <= 0:
            raise ValueError("updateMinutes must be greater than 0")
        return minutes

    @staticmethod
    def _normalize_date_value(value: str | None, *, end_of_day: bool) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if len(text) == 10:
            return f"{text}T23:59:59Z" if end_of_day else f"{text}T00:00:00Z"
        return text

    @staticmethod
    def _derive_next_run(last_run_at: str | None, update_minutes: Any) -> str | None:
        if not last_run_at or update_minutes in (None, ""):
            return None
        try:
            base = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
            next_run = base + timedelta(minutes=int(update_minutes))
        except (TypeError, ValueError):
            return None
        return next_run.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _fts_insert(conn: Any, entry_id: int, title: str, summary: str, content: str) -> None:
        conn.execute(
            "INSERT INTO entries_fts(rowid, title, summary, content) VALUES (?, ?, ?, ?)",
            (entry_id, title or "", summary or "", content or ""),
        )

    @staticmethod
    def _fts_delete(conn: Any, entry_id: int, title: str, summary: str, content: str) -> None:
        conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (entry_id,))


def build_route_handler(store: SoloFeedsStore):
    def handle(handler: Any, method: str, path: str, params: dict[str, list[str]], payload: dict[str, Any]) -> bool:
        try:
            if method == "GET" and path == "/api/snapshot":
                send_json(handler, store.snapshot())
                return True
            if method == "GET" and path == "/api/domains":
                send_json(handler, {"domains": store.list_domains()})
                return True
            if method == "POST" and path == "/api/domains":
                send_json(handler, store.create_domain(str(payload.get("domain") or "")), status=HTTPStatus.CREATED)
                return True
            if method == "GET" and path.startswith("/api/domains/") and path.endswith("/snapshot"):
                domain = urllib.parse.unquote(path.removeprefix("/api/domains/").removesuffix("/snapshot").rstrip("/"))
                snapshot = store.get_domain_snapshot(domain, entry_limit=query_int(params, "limit", 100))
                if snapshot is None:
                    handler.send_error(HTTPStatus.NOT_FOUND, "Domain not found")
                    return True
                send_json(handler, snapshot)
                return True
            if method == "POST" and path.startswith("/api/domains/") and path.endswith("/settings"):
                domain = urllib.parse.unquote(path.removeprefix("/api/domains/").removesuffix("/settings").rstrip("/"))
                send_json(handler, store.update_domain_settings(domain, payload))
                return True
            if method == "DELETE" and path.startswith("/api/domains/"):
                ok = store.delete_domain(urllib.parse.unquote(path.removeprefix("/api/domains/")))
                send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                return True
            if method == "GET" and path == "/api/feeds":
                send_json(handler, {"feeds": store.list_feeds()})
                return True
            if method == "POST" and path == "/api/feeds":
                send_json(handler, store.add_feed(payload), status=HTTPStatus.CREATED)
                return True
            if method == "PATCH" and path.startswith("/api/feeds/") and path.endswith("/rate"):
                feed_id = int(path.removeprefix("/api/feeds/").removesuffix("/rate").rstrip("/"))
                feed = store.update_feed_rate(feed_id, payload.get("updateMinutes") or payload.get("update_minutes") or payload.get("minutes"))
                if feed is None:
                    handler.send_error(HTTPStatus.NOT_FOUND, "Feed not found")
                    return True
                send_json(handler, feed)
                return True
            if method == "DELETE" and path.startswith("/api/feeds/") and path.endswith("/entries"):
                feed_id = int(path.removeprefix("/api/feeds/").removesuffix("/entries").rstrip("/"))
                deleted = store.delete_entries_for_feed(feed_id)
                send_json(handler, {"deleted": deleted})
                return True
            if method == "DELETE" and path.startswith("/api/feeds/"):
                ok = store.delete_feed(int(path.removeprefix("/api/feeds/")))
                send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                return True
            if method == "GET" and path == "/api/entries":
                send_json(
                    handler,
                    {
                        "entries": store.list_entries(
                            limit=query_int(params, "limit", 100),
                            offset=query_int(params, "offset", 0),
                            domain=query_text(params, "domain") or None,
                        )
                    },
                )
                return True
            if method == "POST" and path == "/api/entries":
                send_json(handler, store.add_entry(payload), status=HTTPStatus.CREATED)
                return True
            if method == "GET" and path.startswith("/api/entries/"):
                entry = store.get_entry(int(path.removeprefix("/api/entries/")))
                if entry is None:
                    handler.send_error(HTTPStatus.NOT_FOUND, "Entry not found")
                    return True
                send_json(handler, entry)
                return True
            if method == "PATCH" and path.startswith("/api/entries/"):
                entry = store.update_entry(int(path.removeprefix("/api/entries/")), payload)
                if entry is None:
                    handler.send_error(HTTPStatus.NOT_FOUND, "Entry not found")
                    return True
                send_json(handler, entry)
                return True
            if method == "DELETE" and path.startswith("/api/entries/"):
                ok = store.delete_entry(int(path.removeprefix("/api/entries/")))
                send_json(handler, {"ok": ok}, status=HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
                return True
            if method == "GET" and path == "/api/search":
                send_json(
                    handler,
                    store.search_filtered(
                        query_text(params, "q"),
                        limit=query_int(params, "limit", 20),
                        domain=query_text(params, "domain") or None,
                        since=query_text(params, "since") or None,
                        until=query_text(params, "until") or None,
                        no_older_than_days=_optional_float(query_text(params, "noOlderThanDays")),
                    ),
                )
                return True
            if method == "POST" and path == "/api/search":
                send_json(
                    handler,
                    store.search_filtered(
                        str(payload.get("query") or payload.get("q") or ""),
                        limit=int(payload.get("limit") or 20),
                        domain=blank_to_none(payload.get("domain")),
                        since=blank_to_none(payload.get("since")),
                        until=blank_to_none(payload.get("until")),
                        no_older_than_days=_optional_float(payload.get("noOlderThanDays")),
                    ),
                )
                return True
        except ValueError as exc:
            handler.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return True
        return False

    return handle


def _optional_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError("numeric filter must be a number") from exc


def main() -> int:
    args = parse_service_args("Start the SoloFeeds service.")
    config = load_config()
    host, port = service_host_port(config, "solofeeds", 9742)
    host = args.host or host
    port = int(args.port or port)
    feeds_root = resolve_data_path(config, "SoloData", "Feeds")
    store = SoloFeedsStore(feeds_root)
    if args.command == "status" or args.dry_run:
        print(store.status())
        return 0
    run_http_service(
        label="SoloFeeds",
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
