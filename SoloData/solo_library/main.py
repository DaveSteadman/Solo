# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Launches the SoloLibrary book database service and serves its JSON-specified web UI.

from __future__ import annotations

import argparse
import html
import json
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar


SOLO_LIBRARY_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_LIBRARY_ROOT.parent
SOLO_ROOT = SOLO_DATA_ROOT.parent
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.compression import compress_text, decompress_text  # noqa: E402
from common_utils.sqlite import compute_word_count, fts_build_query, sqlite_connection  # noqa: E402
from common_utils.web import send_json, serve_bounded_file, serve_file  # noqa: E402


CONFIG_DIR = SOLO_ROOT / "Config"
FACTORY_DEFAULT_CONFIG = CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = CONFIG_DIR / "local.json"
UI_DIR = SOLO_LIBRARY_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()
CATALOG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
OPDS_NS = "http://www.w3.org/2005/Atom"


@dataclass(frozen=True)
class CatalogInfo:
    id: str
    label: str
    path: Path
    read_only: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SoloLibrary book database service.")
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument("--host", default=None, help="Bind host.")
    parser.add_argument("--port", type=int, default=None, help="Bind port.")
    parser.add_argument("--dry-run", action="store_true", help="Show configuration without starting the server.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    return merge_dict(load_json(FACTORY_DEFAULT_CONFIG), load_json(LOCAL_CONFIG))


def resolve_solo_path(raw: object, default: str) -> Path:
    path = Path(str(raw or default))
    if path.is_absolute():
        return path.resolve()
    return (SOLO_ROOT / path).resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SoloLibraryStore:
    def __init__(self, library_root: Path, data_root: Path) -> None:
        self.data_root = data_root
        self.library_root = library_root
        self.service_data_root = self.library_root.parent
        self.catalogs_root = self.library_root / "catalogs"
        self.log_dir = self.library_root / "logs"
        self.default_catalog = "local"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.catalogs_root.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self) -> None:
        for catalog in self.list_catalogs(create_default=True):
            if catalog.read_only:
                continue
            with sqlite_connection(catalog.path) as conn:
                self._ensure_schema(conn)

    def list_catalogs(self, create_default: bool = False) -> list[CatalogInfo]:
        if create_default:
            self.catalogs_root.mkdir(parents=True, exist_ok=True)
        catalogs = [
            CatalogInfo(
                id=self.default_catalog,
                label="Local Library",
                path=self.catalogs_root / f"{self.default_catalog}.db",
            )
        ]
        if self.catalogs_root.exists():
            for path in sorted(self.catalogs_root.glob("*.db")):
                catalog_id = self._normalize_catalog(path.stem)
                if catalog_id == self.default_catalog:
                    continue
                catalogs.append(CatalogInfo(catalog_id, self._label_for_catalog(catalog_id), path))
        return catalogs

    def snapshot(self) -> dict[str, Any]:
        catalogs = [self._catalog_snapshot(catalog) for catalog in self.list_catalogs(create_default=True)]
        total_books = sum(item["books"] for item in catalogs)
        return {
            "service": {
                "label": "SoloLibrary",
                "status": "running",
                "root": str(SOLO_LIBRARY_ROOT),
                "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
                "metrics": {
                    "catalogs": len(catalogs),
                    "books": total_books,
                    "dbSizeBytes": sum(item["dbSizeBytes"] for item in catalogs),
                },
            },
            "paths": {
                "soloRoot": str(SOLO_ROOT),
                "dataRoot": str(self.data_root),
                "serviceDataRoot": str(self.service_data_root),
                "libraryRoot": str(self.library_root),
                "catalogsRoot": str(self.catalogs_root),
                "logDir": str(self.log_dir),
            },
            "catalogs": catalogs,
            "recentBooks": self.list_books(limit=12),
        }

    def status(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "service": "SoloLibrary",
            "status": "ok",
            "catalogs": snap["service"]["metrics"]["catalogs"],
            "books": snap["service"]["metrics"]["books"],
            "dataRoot": snap["paths"]["serviceDataRoot"],
        }

    def add_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        catalog_id = self._normalize_catalog(str(payload.get("catalog") or self.default_catalog))
        body = _normalize_imported_body(payload.get("body"))
        now = utc_now()
        with self._connect(catalog_id, create=True) as conn:
            self._ensure_schema(conn)
            compressed_body = compress_text(body)
            word_count = compute_word_count(body)
            cur = conn.execute(
                """
                INSERT INTO books (
                    title, author, year, language, genre, notes, source, source_id,
                    word_count, body, added_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    _blank_to_none(payload.get("author")),
                    _int_or_none(payload.get("year")),
                    _blank_to_none(payload.get("language")),
                    _blank_to_none(payload.get("genre")),
                    _blank_to_none(payload.get("notes")),
                    _blank_to_none(payload.get("source")),
                    _blank_to_none(payload.get("source_id")),
                    word_count,
                    compressed_body,
                    now,
                    now,
                ),
            )
            book_id = int(cur.lastrowid)
            self._fts_insert(conn, book_id, title, str(payload.get("author") or ""), body or "")
            row = conn.execute(self._select_books_sql("WHERE id = ?"), (book_id,)).fetchone()
        return self._row_to_book(row, catalog_id, include_body=False)

    def list_books(self, limit: int = 50, offset: int = 0, catalog: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 250))
        offset = max(0, offset)
        catalogs = [self._catalog_for(catalog)] if catalog else self.list_catalogs(create_default=True)
        books: list[dict[str, Any]] = []
        for item in catalogs:
            with self._connect(item.id) as conn:
                rows = conn.execute(
                    self._select_books_sql("ORDER BY updated_at DESC, title LIMIT ? OFFSET ?"),
                    (limit, offset),
                ).fetchall()
            books.extend(self._row_to_book(row, item.id, include_body=False) for row in rows)
        books.sort(key=lambda item: (str(item.get("updated_at") or ""), str(item.get("title") or "").lower()), reverse=True)
        return books[:limit]

    def search_books(self, query: str, limit: int = 50, catalog: str | None = None) -> dict[str, Any]:
        query = query.strip()
        limit = max(1, min(limit, 250))
        catalogs = [self._catalog_for(catalog)] if catalog else self.list_catalogs(create_default=True)
        if not query:
            return {"query": query, "results": [], "catalogsSearched": [item.id for item in catalogs]}
        fts_query = fts_build_query(query)
        if not fts_query:
            return {"query": query, "results": [], "catalogsSearched": [item.id for item in catalogs]}
        results: list[dict[str, Any]] = []
        for item in catalogs:
            with self._connect(item.id) as conn:
                rows = conn.execute(
                    f"""
                    SELECT b.id, b.title, b.author, b.year, b.language, b.genre, b.notes,
                           b.source, b.source_id, b.word_count, b.added_at, b.updated_at,
                           snippet(books_fts, 2, '[', ']', '...', 28) AS snippet
                    FROM books_fts
                    JOIN books b ON b.id = books_fts.rowid
                    WHERE books_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            for row in rows:
                book = self._row_to_book(row, item.id, include_body=False)
                book["snippet"] = row["snippet"]
                results.append(book)
        return {
            "query": query,
            "catalogsSearched": [item.id for item in catalogs],
            "results": results[:limit],
        }

    def get_book(self, route_id: str) -> dict[str, Any] | None:
        catalog_id, local_id = self._parse_route_id(route_id)
        with self._connect(catalog_id) as conn:
            row = conn.execute(self._select_books_sql("WHERE id = ?", include_body=True), (local_id,)).fetchone()
        return self._row_to_book(row, catalog_id, include_body=True) if row else None

    def update_book(self, route_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        catalog_id, local_id = self._parse_route_id(route_id)
        allowed = ("title", "author", "year", "language", "genre", "notes", "source", "source_id", "body")
        fields = {key: payload[key] for key in allowed if key in payload}
        if not fields:
            return self.get_book(route_id)
        if "title" in fields and not str(fields["title"] or "").strip():
            raise ValueError("title is required")

        with self._connect(catalog_id) as conn:
            current = conn.execute("SELECT title, author, body FROM books WHERE id = ?", (local_id,)).fetchone()
            if current is None:
                return None

            assignments: list[str] = []
            values: list[Any] = []
            for key in allowed:
                if key not in fields:
                    continue
                if key == "body":
                    body = str(fields[key] or "") or None
                    assignments.extend(["body = ?", "word_count = ?"])
                    values.extend([compress_text(body), compute_word_count(body)])
                elif key == "year":
                    assignments.append("year = ?")
                    values.append(_int_or_none(fields[key]))
                else:
                    assignments.append(f"{key} = ?")
                    values.append(_blank_to_none(fields[key]) if key != "title" else str(fields[key]).strip())

            assignments.append("updated_at = ?")
            values.append(utc_now())
            values.append(local_id)

            self._fts_delete(conn, local_id, current["title"] or "", current["author"] or "", decompress_text(current["body"]) or "")
            conn.execute(f"UPDATE books SET {', '.join(assignments)} WHERE id = ?", values)
            updated = conn.execute("SELECT title, author, body FROM books WHERE id = ?", (local_id,)).fetchone()
            self._fts_insert(conn, local_id, updated["title"] or "", updated["author"] or "", decompress_text(updated["body"]) or "")
            row = conn.execute(self._select_books_sql("WHERE id = ?", include_body=True), (local_id,)).fetchone()
        return self._row_to_book(row, catalog_id, include_body=True)

    def delete_book(self, route_id: str) -> bool:
        catalog_id, local_id = self._parse_route_id(route_id)
        with self._connect(catalog_id) as conn:
            row = conn.execute("SELECT title, author, body FROM books WHERE id = ?", (local_id,)).fetchone()
            if row is None:
                return False
            self._fts_delete(conn, local_id, row["title"] or "", row["author"] or "", decompress_text(row["body"]) or "")
            conn.execute("DELETE FROM books WHERE id = ?", (local_id,))
        return True

    def title_exists(self, title: str, catalog: str | None = None) -> bool:
        title = title.strip()
        if not title:
            return False
        catalogs = [self._catalog_for(catalog)] if catalog else self.list_catalogs(create_default=True)
        for item in catalogs:
            with self._connect(item.id) as conn:
                row = conn.execute(
                    "SELECT 1 FROM books WHERE lower(title) = lower(?) LIMIT 1",
                    (title,),
                ).fetchone()
            if row is not None:
                return True
        return False

    def import_kiwix_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        kiwix_url = str(payload.get("kiwix_url") or "").strip()
        zim_name = str(payload.get("zim_name") or "").strip()
        title = str(payload.get("title") or "").strip()
        article_url = str(payload.get("article_url") or "").strip()
        catalog = str(payload.get("catalog") or self.default_catalog)
        if not kiwix_url:
            raise KiwixError("kiwix_url not configured", HTTPStatus.SERVICE_UNAVAILABLE)
        if not zim_name:
            raise ValueError("zim_name is required")
        if not title:
            raise ValueError("title is required")
        if self.title_exists(title, catalog=catalog):
            raise KiwixError(f"Already imported: {title!r}", HTTPStatus.CONFLICT)

        host = kiwix_url.rstrip("/")
        if article_url:
            content_url = host + article_url
        else:
            encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
            content_url = f"{host}/content/{urllib.parse.quote(zim_name, safe='')}/A/{encoded}"

        text = _kiwix_get_text(content_url, timeout=30)
        parsed = _parse_gutenberg_html(text)
        return self.add_book(
            {
                "title": title,
                "body": parsed["body"],
                "author": payload.get("author") or parsed["author"],
                "year": payload.get("year") or parsed["year"],
                "language": payload.get("language") or "en",
                "genre": parsed["genre"],
                "source": "kiwix",
                "source_id": article_url or content_url,
                "catalog": catalog,
            }
        )

    def import_kiwix_viewer_url(
        self,
        viewer_url: str,
        language: str = "en",
        kiwix_url: str | None = None,
        catalog: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"url": viewer_url, "status": "error", "title": None, "id": None, "detail": None}
        try:
            parsed_url = urllib.parse.urlparse(viewer_url)
            host = (kiwix_url or f"{parsed_url.scheme}://{parsed_url.netloc}").rstrip("/")
            fragment = parsed_url.fragment
            if not host or not fragment or "/" not in fragment:
                result["detail"] = "URL must contain a Kiwix host and a fragment like #zim/Article"
                return result
            zim, article_path = fragment.split("/", 1)
            content_url = f"{host}/content/{zim}/{article_path}"
            text = _kiwix_get_text(content_url, timeout=30)
            parsed = _parse_gutenberg_html(text)
            title = parsed["title"] or _title_from_article_path(article_path)
            result["title"] = title
            if self.title_exists(title, catalog=catalog):
                result["status"] = "exists"
                result["detail"] = f"Already imported: {title!r}"
                return result
            book = self.add_book(
                {
                    "title": title,
                    "body": parsed["body"],
                    "author": parsed["author"],
                    "year": parsed["year"],
                    "language": language or "en",
                    "genre": parsed["genre"],
                    "source": "kiwix",
                    "source_id": viewer_url,
                    "catalog": catalog or self.default_catalog,
                }
            )
            result["status"] = "ok"
            result["id"] = book["route_id"]
        except Exception as exc:
            result["detail"] = str(exc)
        return result

    def _catalog_snapshot(self, catalog: CatalogInfo) -> dict[str, Any]:
        with self._connect(catalog.id, create=True) as conn:
            books = int(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0])
            no_body = int(conn.execute("SELECT COUNT(*) FROM books WHERE body IS NULL OR body = ''").fetchone()[0])
            incomplete = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM books
                    WHERE author IS NULL OR author = ''
                       OR year IS NULL
                       OR language IS NULL OR language = ''
                       OR genre IS NULL OR genre = ''
                    """
                ).fetchone()[0]
            )
        return {
            "id": catalog.id,
            "label": catalog.label,
            "path": str(catalog.path),
            "readOnly": catalog.read_only,
            "books": books,
            "incomplete": incomplete,
            "withoutBody": no_body,
            "dbSizeBytes": catalog.path.stat().st_size if catalog.path.exists() else 0,
            "updated": _mtime(catalog.path),
        }

    def _connect(self, catalog: str, create: bool = False):
        info = self._catalog_for(catalog, create=create)
        return sqlite_connection(info.path, create_parent=not info.read_only)

    def _catalog_for(self, catalog: str | None, create: bool = False) -> CatalogInfo:
        catalog_id = self._normalize_catalog(catalog or self.default_catalog)
        for item in self.list_catalogs(create_default=create):
            if item.id == catalog_id:
                return item
        if create:
            return CatalogInfo(catalog_id, self._label_for_catalog(catalog_id), self.catalogs_root / f"{catalog_id}.db")
        raise ValueError(f"Unknown catalog: {catalog_id}")

    @staticmethod
    def _ensure_schema(conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                author      TEXT,
                year        INTEGER,
                language    TEXT,
                genre       TEXT,
                notes       TEXT,
                source      TEXT,
                source_id   TEXT,
                word_count  INTEGER,
                body        BLOB,
                added_at    TEXT,
                updated_at  TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                title, author, body,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_books_source_id ON books(source, source_id) "
            "WHERE source_id IS NOT NULL AND source_id != ''"
        )

    @staticmethod
    def _select_books_sql(suffix: str, include_body: bool = False) -> str:
        cols = [
            "id", "title", "author", "year", "language", "genre", "notes",
            "source", "source_id", "word_count", "added_at", "updated_at",
        ]
        if include_body:
            cols.append("body")
        return f"SELECT {', '.join(cols)} FROM books {suffix}"

    @staticmethod
    def _fts_insert(conn, book_id: int, title: str, author: str, body: str) -> None:
        conn.execute(
            "INSERT INTO books_fts(rowid, title, author, body) VALUES (?, ?, ?, ?)",
            (book_id, title or "", author or "", body or ""),
        )

    @staticmethod
    def _fts_delete(conn, book_id: int, title: str, author: str, body: str) -> None:
        conn.execute(
            "INSERT INTO books_fts(books_fts, rowid, title, author, body) VALUES ('delete', ?, ?, ?, ?)",
            (book_id, title or "", author or "", body or ""),
        )

    def _row_to_book(self, row, catalog: str, include_body: bool = False) -> dict[str, Any]:
        data = {key: row[key] for key in row.keys() if key != "body"}
        data["catalog"] = catalog
        data["route_id"] = f"{catalog}-{data['id']}"
        data["author_short_name"] = _author_short_name(data.get("author"))
        if include_body:
            data["body"] = decompress_text(row["body"])
        return data

    @staticmethod
    def _normalize_catalog(catalog: str) -> str:
        value = str(catalog or "local").strip().lower() or "local"
        if not CATALOG_RE.fullmatch(value):
            raise ValueError(f"Invalid catalog id: {catalog!r}")
        return value

    @staticmethod
    def _label_for_catalog(catalog: str) -> str:
        return catalog.replace("_", " ").replace("-", " ").title()

    def _parse_route_id(self, route_id: str) -> tuple[str, int]:
        text = str(route_id).strip()
        if ":" in text:
            catalog, local_id = text.split(":", 1)
            return self._normalize_catalog(catalog), int(local_id)
        if "-" in text:
            catalog, local_id = text.rsplit("-", 1)
            if local_id.isdigit():
                return self._normalize_catalog(catalog), int(local_id)
        return self.default_catalog, int(text)


class KiwixError(RuntimeError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_GATEWAY) -> None:
        super().__init__(message)
        self.status = status


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.heading = ""
        self.meta: dict[str, str] = {}
        self._skip = 0
        self._in_title = False
        self._in_heading = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "nav", "header", "footer"}:
            self._skip += 1
            return
        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            content = attrs_dict.get("content", "")
            if name and content:
                self.meta[name] = content
            return
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_heading = True
        if tag in {"p", "br", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer"} and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_heading = False
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += data
        if self._in_heading and not self.heading:
            self.heading += data
        if self._skip:
            return
        self._parts.append(data)

    def body(self) -> str:
        text = html.unescape(" ".join(self._parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n+ *", "\n\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _parse_gutenberg_html(raw_html: str) -> dict[str, Any]:
    parser = _ReadableHtmlParser()
    parser.feed(raw_html)
    meta = parser.meta
    title = (
        meta.get("dc.title")
        or meta.get("title")
        or meta.get("citation_title")
        or _clean_title(parser.title)
        or parser.heading.strip()
        or None
    )
    author = meta.get("author") or meta.get("dc.creator") or meta.get("citation_author")
    year = None
    date = meta.get("dc.date") or meta.get("date") or meta.get("citation_date")
    if date:
        match = re.search(r"\d{4}", date)
        if match:
            year = int(match.group(0))
    return {
        "body": parser.body(),
        "author": author,
        "year": year,
        "title": title,
        "genre": meta.get("dc.subject"),
    }


def _clean_title(value: str) -> str | None:
    text = html.unescape(value or "").strip()
    text = re.sub(r"\s*[\|\-]\s*.{3,80}$", "", text).strip()
    return text or None


def _author_short_name(value: Any) -> str | None:
    text = " ".join(str(value or "").split()).strip(" ,")
    if not text:
        return None

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2 and not re.search(r"\d", parts[0]):
        surname = parts[0]
        given = [part for part in parts[1:] if not re.search(r"\d", part)]
        if given:
            return " ".join(given + [surname]).strip()
        return surname

    text = re.sub(r",?\s*\d{2,4}\??\s*(?:BCE|CE|BC|AD)?\s*[-–]\s*\d{2,4}\??\s*(?:BCE|CE|BC|AD)?$", "", text, flags=re.IGNORECASE).strip(" ,")
    text = re.sub(r",?\s*\d{2,4}\??\s*(?:BCE|CE|BC|AD)?$", "", text, flags=re.IGNORECASE).strip(" ,")
    return text or None


def _title_from_article_path(article_path: str) -> str:
    text = urllib.parse.unquote(article_path.rsplit("/", 1)[-1])
    text = re.sub(r"\.\d+$", "", text)
    return text.replace("_", " ").strip() or "Untitled"


def _normalize_imported_body(value: Any) -> str | None:
    text = str(value or "")
    if not text.strip():
        return None

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if not text:
        return None

    blocks = [block for block in re.split(r"\n{2,}", text) if block.strip()]
    normalized = [_normalize_import_block(block) for block in blocks]
    normalized = [block for block in normalized if block]
    return "\n\n".join(normalized) or None


def _normalize_import_block(block: str) -> str:
    lines = [re.sub(r"\s+", " ", line.strip()) for line in block.split("\n") if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]

    lengths = sorted(len(line) for line in lines)
    median = lengths[len(lengths) // 2]
    average = sum(lengths) / len(lengths)
    dialogue_cues = sum(1 for line in lines if re.match(r'^(?:["\'\u2018\u201c-]|[A-Z][A-Za-z]+:)', line))

    if median >= 60 or (average >= 50 and lengths[-1] >= 72):
        if dialogue_cues * 2 < len(lines) or average >= 65:
            return " ".join(lines)

    return "\n".join(lines)


def _kiwix_get_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "SoloLibrary/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise KiwixError(f"Not found in Kiwix: {url}", HTTPStatus.NOT_FOUND) from exc
        raise KiwixError(f"Kiwix returned HTTP {exc.code}: {url}", HTTPStatus.BAD_GATEWAY) from exc
    except urllib.error.URLError as exc:
        raise KiwixError(f"Kiwix unreachable: {exc.reason}", HTTPStatus.BAD_GATEWAY) from exc


def _kiwix_inventory(kiwix_url: str) -> dict[str, Any]:
    if not kiwix_url:
        raise KiwixError("kiwix_url not configured", HTTPStatus.SERVICE_UNAVAILABLE)
    text = _kiwix_get_text(f"{kiwix_url.rstrip('/')}/catalog/v2/entries?count=-1", timeout=10)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise KiwixError(f"Kiwix OPDS parse error: {exc}") from exc
    books = []
    for entry in root.findall(f"{{{OPDS_NS}}}entry"):
        title = entry.findtext(f"{{{OPDS_NS}}}title", default="") or ""
        author_el = entry.find(f"{{{OPDS_NS}}}author/{{{OPDS_NS}}}name")
        author = author_el.text if author_el is not None else ""
        zim_name = ""
        for link in entry.findall(f"{{{OPDS_NS}}}link"):
            if link.get("type") == "text/html":
                parts = link.get("href", "").strip("/").split("/")
                zim_name = parts[1] if len(parts) > 1 and parts[0] == "content" else parts[0]
                break
        if zim_name:
            books.append({"name": zim_name, "title": title, "author": author})
    return {"books": books}


def _extract_xml_link(element) -> str:
    if element is None:
        return ""
    if element.text and element.text.strip():
        return element.text.strip()
    return element.get("href", "").strip()


def _kiwix_search(kiwix_url: str, zim: str, query: str, count: int) -> list[dict[str, Any]]:
    if not kiwix_url:
        raise KiwixError("kiwix_url not configured", HTTPStatus.SERVICE_UNAVAILABLE)
    params = urllib.parse.urlencode({"books.name": zim, "pattern": query, "format": "xml", "pageLength": count})
    text = _kiwix_get_text(f"{kiwix_url.rstrip('/')}/search?{params}", timeout=15)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise KiwixError(f"Kiwix search parse error: {exc}") from exc
    results = []
    candidates = []
    for channel in root.iter("channel"):
        candidates.extend(channel.findall("item"))
    candidates.extend(root.iter("result"))
    for item in candidates:
        title = (item.findtext("title") or "").strip()
        snippet = (item.findtext("description") or item.findtext("snippet") or "").strip()
        snippet = re.sub(r"<[^>]+>", " ", html.unescape(snippet)).strip()
        article_url = urllib.parse.urlparse(_extract_xml_link(item.find("link"))).path or None
        if title:
            results.append({"label": title, "value": title, "snippet": snippet, "url": article_url})
    return results


def _kiwix_suggest(kiwix_url: str, zim: str, pattern: str, count: int) -> list[dict[str, Any]]:
    if not kiwix_url:
        raise KiwixError("kiwix_url not configured", HTTPStatus.SERVICE_UNAVAILABLE)
    params = urllib.parse.urlencode({"content": zim, "term": pattern, "count": count})
    text = _kiwix_get_text(f"{kiwix_url.rstrip('/')}/suggest?{params}", timeout=10)
    items = json.loads(text)
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_label = item.get("label") or item.get("value") or ""
        if "/search?" in str(item.get("url", "")) or not item.get("value") or re.search(r"containing\s+['\"]", raw_label, re.IGNORECASE):
            continue
        label = re.sub(r"<[^>]+>", "", html.unescape(str(raw_label))).strip()
        value = re.sub(r"<[^>]+>", "", html.unescape(str(item.get("value") or label))).strip()
        if not item.get("url"):
            item["url"] = f"/content/{zim}/A/{urllib.parse.quote(value.replace(' ', '_'), safe='')}"
        item["label"] = label
        item["value"] = value
        cleaned.append(item)
    return cleaned


def _kiwix_catalog(kiwix_url: str, zim: str, author: str | None = None) -> dict[str, Any]:
    if not kiwix_url:
        raise KiwixError("kiwix_url not configured", HTTPStatus.SERVICE_UNAVAILABLE)
    text = _kiwix_get_text(f"{kiwix_url.rstrip('/')}/content/{zim}/full_by_popularity.js", timeout=30)
    match = re.search(r"var\s+json_data\s*=\s*(\[.*?\])\s*;?\s*$", text, re.DOTALL)
    if not match:
        raise KiwixError("Could not locate json_data in catalog JS file")
    raw = json.loads(match.group(1))
    author_filter = author.lower() if author else None
    grouped: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for entry in raw:
        if not isinstance(entry, list) or len(entry) < 4:
            continue
        title = str(entry[0]).strip()
        author_name = str(entry[1]).strip()
        if author_filter and author_filter not in author_name.lower():
            continue
        slug = title.replace("/", "-")[:230] + "." + str(entry[3])
        encoded_slug = urllib.parse.quote(slug)
        grouped.setdefault(author_name, []).append(
            {
                "title": title,
                "gutenberg_id": entry[3],
                "article_path": f"/content/{zim}/{encoded_slug}",
                "viewer_url": f"{kiwix_url.rstrip('/')}/viewer#{zim}/{encoded_slug}",
            }
        )
        total += 1
    return {
        "total": total,
        "authors": [
            {"author": name, "books": sorted(books, key=lambda item: item["title"])}
            for name, books in sorted(grouped.items())
        ],
    }


def build_handler(store: SoloLibraryStore):
    class LibraryHandler(BaseHTTPRequestHandler):
        store_ref: ClassVar[SoloLibraryStore] = store

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query)
            try:
                if path in ("", "/", "/ui"):
                    serve_file(self, UI_DIR / "index.html")
                    return
                if path == "/status":
                    send_json(self, self.store_ref.status())
                    return
                if path == "/api/snapshot":
                    send_json(self, self.store_ref.snapshot())
                    return
                if path == "/api/catalogs":
                    send_json(self, {"catalogs": self.store_ref.snapshot()["catalogs"]})
                    return
                if path == "/api/books":
                    send_json(
                        self,
                        {
                            "books": self.store_ref.list_books(
                                limit=_query_int(params, "limit", 50),
                                offset=_query_int(params, "offset", 0),
                            )
                        },
                    )
                    return
                if path.startswith("/api/books/"):
                    book = self.store_ref.get_book(urllib.parse.unquote(path.removeprefix("/api/books/")))
                    if book is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "Book not found")
                        return
                    send_json(self, book)
                    return
                if path == "/api/search":
                    query = (params.get("q") or [""])[0]
                    send_json(self, self.store_ref.search_books(query, limit=_query_int(params, "limit", 50)))
                    return
                if path == "/api/import/kiwix/inventory":
                    send_json(self, _kiwix_inventory(_query_text(params, "kiwix_url")))
                    return
                if path == "/api/import/kiwix/search":
                    send_json(
                        self,
                        _kiwix_search(
                            _query_text(params, "kiwix_url"),
                            _query_text(params, "zim"),
                            _query_text(params, "q"),
                            _query_int(params, "count", 100),
                        ),
                    )
                    return
                if path == "/api/import/kiwix/suggest":
                    send_json(
                        self,
                        _kiwix_suggest(
                            _query_text(params, "kiwix_url"),
                            _query_text(params, "zim"),
                            _query_text(params, "pattern"),
                            _query_int(params, "count", 50),
                        ),
                    )
                    return
                if path == "/api/import/kiwix/catalog":
                    send_json(
                        self,
                        _kiwix_catalog(
                            _query_text(params, "kiwix_url"),
                            _query_text(params, "zim"),
                            _query_text(params, "author") or None,
                        ),
                    )
                    return
                if path.startswith("/ui/"):
                    relative_path = path.removeprefix("/ui/")
                    if "." not in Path(relative_path).name:
                        serve_file(self, UI_DIR / "index.html")
                        return
                    serve_bounded_file(self, UI_DIR, relative_path)
                    return
                if path.startswith("/common/"):
                    serve_bounded_file(self, COMMON_UI_DIR, path.removeprefix("/common/"))
                    return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except KiwixError as exc:
                send_json(self, {"detail": str(exc)}, status=exc.status)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == "/api/books":
                    send_json(self, self.store_ref.add_book(self._read_json()), status=HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/search":
                    payload = self._read_json()
                    query = str(payload.get("query") or payload.get("q") or "")
                    limit = _parse_int(str(payload.get("limit") or "50"), 50)
                    send_json(self, self.store_ref.search_books(query, limit=limit))
                    return
                if parsed.path == "/api/import/kiwix":
                    send_json(self, self.store_ref.import_kiwix_book(self._read_json()), status=HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/import/kiwix/viewer":
                    payload = self._read_json()
                    result = self.store_ref.import_kiwix_viewer_url(
                        str(payload.get("viewer_url") or ""),
                        language=str(payload.get("language") or "en"),
                        kiwix_url=_blank_to_none(payload.get("kiwix_url")),
                        catalog=_blank_to_none(payload.get("catalog")),
                    )
                    if result["status"] == "exists":
                        send_json(self, {"detail": result["detail"]}, status=HTTPStatus.CONFLICT)
                        return
                    if result["status"] == "error":
                        send_json(self, {"detail": result["detail"]}, status=HTTPStatus.BAD_GATEWAY)
                        return
                    book = self.store_ref.get_book(str(result["id"]))
                    send_json(self, book, status=HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/import/kiwix/viewer/batch":
                    payload = self._read_json()
                    urls = payload.get("urls") if isinstance(payload.get("urls"), list) else []
                    results = [
                        self.store_ref.import_kiwix_viewer_url(
                            str(raw).strip(),
                            language=str(payload.get("language") or "en"),
                            kiwix_url=_blank_to_none(payload.get("kiwix_url")),
                            catalog=_blank_to_none(payload.get("catalog")),
                        )
                        for raw in urls
                        if str(raw).strip() and not str(raw).strip().startswith("#")
                    ]
                    summary = {
                        "ok": sum(1 for item in results if item["status"] == "ok"),
                        "exists": sum(1 for item in results if item["status"] == "exists"),
                        "error": sum(1 for item in results if item["status"] == "error"),
                    }
                    send_json(self, {"results": results, "summary": summary})
                    return
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except KiwixError as exc:
                send_json(self, {"detail": str(exc)}, status=exc.status)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.startswith("/api/books/"):
                try:
                    book = self.store_ref.update_book(
                        urllib.parse.unquote(parsed.path.removeprefix("/api/books/")),
                        self._read_json(),
                    )
                except ValueError as exc:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                if book is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Book not found")
                    return
                send_json(self, book)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.startswith("/api/books/"):
                try:
                    deleted = self.store_ref.delete_book(urllib.parse.unquote(parsed.path.removeprefix("/api/books/")))
                except ValueError as exc:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                if not deleted:
                    self.send_error(HTTPStatus.NOT_FOUND, "Book not found")
                    return
                send_json(self, {"ok": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = _parse_int(self.headers.get("Content-Length", "0"), 0)
            if length <= 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}

    return LibraryHandler


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _query_int(params: dict[str, list[str]], name: str, default: int) -> int:
    return _parse_int((params.get(name) or [str(default)])[0], default)


def _query_text(params: dict[str, list[str]], name: str) -> str:
    return str((params.get(name) or [""])[0]).strip()


def _blank_to_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _mtime(path: Path) -> str:
    if not path.exists():
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))


def serve(store: SoloLibraryStore, host: str, port: int, stop_event: threading.Event) -> None:
    httpd = ThreadingHTTPServer((host, port), build_handler(store))
    httpd.timeout = 0.5
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()


def print_status(store: SoloLibraryStore, host: str, port: int) -> None:
    snapshot = store.snapshot()
    print("SoloLibrary status")
    print(f"  url        http://{host}:{port}/")
    print(f"  data       {snapshot['paths']['libraryRoot']}")
    print(f"  catalogs   {snapshot['service']['metrics']['catalogs']}")
    print(f"  books      {snapshot['service']['metrics']['books']}")


def main() -> int:
    args = parse_args()
    config = load_config()
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    sololibrary = services.get("sololibrary") if isinstance(services.get("sololibrary"), dict) else {}
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    host = args.host or str(sololibrary.get("host") or network.get("host") or "127.0.0.1")
    port = int(args.port or sololibrary.get("port") or 9741)
    data_root = resolve_solo_path(paths.get("dataRoot"), "./Data")
    library_root = resolve_solo_path(paths.get("soloDataLibraryRoot"), "./Data/SoloData/Library")
    store = SoloLibraryStore(library_root, data_root)

    if args.command == "status" or args.dry_run:
        print_status(store, host, port)
        return 0

    stop_event = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    print(f"SoloLibrary: http://{host}:{port}/", flush=True)
    serve(store, host, port, stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
