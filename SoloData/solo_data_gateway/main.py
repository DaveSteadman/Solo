# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Runs the SoloData gateway web UI, starts child data services, and exposes MCP tools.

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import FastMCP


SOLO_DATA_GATEWAY_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_DATA_GATEWAY_ROOT.parent
SOLO_ROOT = SOLO_DATA_ROOT.parent
CONFIG_DIR = SOLO_ROOT / "Config"
FACTORY_DEFAULT_CONFIG = CONFIG_DIR / "factory-default.json"
LOCAL_CONFIG = CONFIG_DIR / "local.json"
UI_DIR = SOLO_DATA_ROOT / "ui"
COMMON_UI_DIR = SOLO_ROOT / "SoloCommonWebUI"
STARTED_AT = time.monotonic()
CHUNK_SIZE = 8000


@dataclass(frozen=True)
class ServiceEndpoint:
    slug: str
    label: str
    description: str
    folder: Path
    base_url: str = ""
    ui_url: str = ""
    status_url: str = ""
    search_url: str = ""


@dataclass
class ChildProcess:
    label: str
    cwd: Path
    command: list[str]
    log_path: Path
    base_url: str
    process: subprocess.Popen | None = None
    log_file: Any = None
    external: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SoloData gateway service.")
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


def service_base_url(config: dict[str, Any], slug: str, fallback_port: int) -> str:
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service = services.get(slug) if isinstance(services.get(slug), dict) else {}
    host = str(service.get("host") or network.get("host") or "127.0.0.1")
    port = int(service.get("port") or fallback_port)
    return f"http://{host}:{port}"


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_limit(value: int, default: int = 20, maximum: int = 200) -> int:
    return max(1, min(_parse_int(value, default), maximum))


def _safe_url_path(path: str) -> str:
    return quote(path, safe="/-_.")


def build_artifact_ref(kind: str, **parts: Any) -> str:
    segments = [kind]
    for key, value in parts.items():
        segments.append(f"{key}={quote(str(value or ''), safe='')}")
    return "|".join(segments)


def parse_artifact_ref(refid: str) -> tuple[str, dict[str, str]]:
    parts = [part for part in str(refid or "").split("|") if part]
    if not parts:
        return "", {}
    values: dict[str, str] = {}
    for segment in parts[1:]:
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        values[key] = unquote(value)
    return parts[0], values


def map_library_result(item: dict[str, Any], ui_url: str) -> dict[str, Any]:
    route_id = str(item.get("route_id") or item.get("id") or "")
    return {
        "type": "library_book",
        "artifact_ref": build_artifact_ref("library_book", book_id=route_id),
        "id": route_id,
        "local_id": item.get("id"),
        "catalog": item.get("catalog"),
        "route_id": route_id,
        "title": item.get("title", ""),
        "author": item.get("author", ""),
        "source": "library",
        "path": route_id,
        "snippet": item.get("snippet") or item.get("notes") or "",
        "url": f"{ui_url.rstrip('/')}/books/{_safe_url_path(route_id)}" if ui_url and route_id else "",
    }


def map_feed_result(item: dict[str, Any], ui_url: str) -> dict[str, Any]:
    entry_id = str(item.get("id") or "")
    return {
        "type": "feed_entry",
        "artifact_ref": build_artifact_ref("feed_entry", entry_id=entry_id),
        "id": entry_id,
        "title": item.get("title", ""),
        "source": "feeds",
        "domain": item.get("domain"),
        "path": item.get("url") or entry_id,
        "snippet": item.get("snippet") or item.get("summary") or "",
        "url": item.get("url") or (f"{ui_url.rstrip('/')}/entries/{entry_id}" if ui_url and entry_id else ""),
        "published_at": item.get("published_at"),
    }


def map_reference_result(item: dict[str, Any], ui_url: str) -> dict[str, Any]:
    title = str(item.get("title") or "")
    return {
        "type": "reference_article",
        "artifact_ref": build_artifact_ref("reference_article", title=title),
        "id": title,
        "title": title,
        "source": "reference",
        "path": title,
        "snippet": item.get("snippet") or item.get("summary") or "",
        "url": f"{ui_url.rstrip('/')}/articles/{_safe_url_path(title)}" if ui_url and title else "",
        "word_count": item.get("word_count"),
    }


def map_rag_result(item: dict[str, Any], ui_url: str) -> dict[str, Any]:
    chunk_id = str(item.get("id") or "")
    db = str(item.get("db") or "default")
    return {
        "type": "rag_chunk",
        "artifact_ref": build_artifact_ref("rag_chunk", db=db, chunk_id=chunk_id),
        "id": chunk_id,
        "db": db,
        "title": item.get("title", ""),
        "source": "rag",
        "path": f"{db}/{chunk_id}",
        "snippet": item.get("snippet") or "",
        "url": f"{ui_url.rstrip('/')}/chunks/{chunk_id}" if ui_url and chunk_id else "",
        "tags": item.get("tags"),
    }


def map_graph_result(item: dict[str, Any], ui_url: str) -> dict[str, Any]:
    relation_id = str(item.get("id") or "")
    title = f"{item.get('start', '')} {item.get('predicate', '')} {item.get('end', '')}".strip()
    return {
        "type": "graph_connection",
        "artifact_ref": build_artifact_ref("graph_connection", connection_id=relation_id),
        "id": relation_id,
        "title": title,
        "source": "graph",
        "path": relation_id,
        "snippet": item.get("evidence") or title,
        "url": f"{ui_url.rstrip('/')}/connections/{relation_id}" if ui_url and relation_id else "",
        "score": item.get("score"),
    }


def flatten_search_results(results_by_domain: dict[str, Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    row_index = 0
    while True:
        added = False
        for domain in ("feeds", "library", "reference", "rag", "graph"):
            items = results_by_domain.get(domain)
            if isinstance(items, list) and row_index < len(items):
                merged.append(items[row_index])
                added = True
        if not added:
            break
        row_index += 1
    return merged


class SoloDataGateway:
    def __init__(self, config: dict[str, Any]) -> None:
        paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
        self.config = config
        self.data_root = resolve_solo_path(paths.get("dataRoot"), "./Data")
        self.service_data_root = self.data_root / "SoloData"
        self.feeds_root = self.data_root / "SoloData" / "Feeds"
        self.library_root = self.data_root / "SoloData" / "Library"
        self.reference_root = self.data_root / "SoloData" / "Reference"
        self.rag_root = self.data_root / "SoloData" / "RAG"
        self.graph_root = self.data_root / "SoloGraph"
        self.log_dir = self.service_data_root / "logs"
        self.feeds_base_url = service_base_url(config, "solofeeds", 9742)
        self.library_base_url = service_base_url(config, "sololibrary", 9741)
        self.reference_base_url = service_base_url(config, "soloreference", 9743)
        self.rag_base_url = service_base_url(config, "solorag", 9744)
        self.graph_base_url = service_base_url(config, "solograph", 9745)
        self.sources = [
            ServiceEndpoint(
                "feeds",
                "Feeds",
                "Imported web feeds and current articles.",
                self.feeds_root,
                self.feeds_base_url,
                f"{self.feeds_base_url}/ui",
                f"{self.feeds_base_url}/status",
                f"{self.feeds_base_url}/api/search",
            ),
            ServiceEndpoint(
                "library",
                "Library",
                "Books and long-form source material.",
                self.library_root,
                self.library_base_url,
                f"{self.library_base_url}/ui",
                f"{self.library_base_url}/status",
                f"{self.library_base_url}/api/search",
            ),
            ServiceEndpoint(
                "reference",
                "Reference",
                "Reference articles and encyclopedia-like notes.",
                self.reference_root,
                self.reference_base_url,
                f"{self.reference_base_url}/ui",
                f"{self.reference_base_url}/status",
                f"{self.reference_base_url}/api/search",
            ),
            ServiceEndpoint(
                "rag",
                "RAG",
                "Chunked documents and retrieval records.",
                self.rag_root,
                self.rag_base_url,
                f"{self.rag_base_url}/ui",
                f"{self.rag_base_url}/status",
                f"{self.rag_base_url}/api/search",
            ),
            ServiceEndpoint(
                "graph",
                "Graph",
                "Knowledge graph nodes and connections.",
                self.graph_root,
                self.graph_base_url,
                f"{self.graph_base_url}/ui",
                f"{self.graph_base_url}/status",
                f"{self.graph_base_url}/api/search",
            ),
        ]
        self.children = [
            ChildProcess(
                label="SoloFeeds",
                cwd=SOLO_DATA_ROOT / "solo_feeds",
                command=[sys.executable, "main.py"],
                log_path=self.feeds_root / "logs" / "service.log",
                base_url=self.feeds_base_url,
            ),
            ChildProcess(
                label="SoloLibrary",
                cwd=SOLO_DATA_ROOT / "solo_library",
                command=[sys.executable, "main.py"],
                log_path=self.library_root / "logs" / "service.log",
                base_url=self.library_base_url,
            ),
            ChildProcess(
                label="SoloReference",
                cwd=SOLO_DATA_ROOT / "solo_reference",
                command=[sys.executable, "main.py"],
                log_path=self.reference_root / "logs" / "service.log",
                base_url=self.reference_base_url,
            ),
            ChildProcess(
                label="SoloRAG",
                cwd=SOLO_DATA_ROOT / "solo_rag",
                command=[sys.executable, "main.py"],
                log_path=self.rag_root / "logs" / "service.log",
                base_url=self.rag_base_url,
            ),
            ChildProcess(
                label="SoloGraph",
                cwd=SOLO_DATA_ROOT / "solo_graph",
                command=[sys.executable, "main.py"],
                log_path=self.graph_root / "logs" / "service.log",
                base_url=self.graph_base_url,
            ),
        ]
        self.clients: dict[str, httpx.AsyncClient] = {}
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for source in self.sources:
            source.folder.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        sources = [self._source_snapshot(source) for source in self.sources]
        return {
            "service": {
                "label": "SoloData",
                "status": "running",
                "root": str(SOLO_DATA_ROOT),
                "uptimeSec": round(time.monotonic() - STARTED_AT, 1),
                "metrics": {
                    "sources": len(sources),
                    "existingFolders": sum(1 for item in sources if item["exists"]),
                    "files": sum(item["fileCount"] for item in sources),
                },
            },
            "paths": {
                "soloRoot": str(SOLO_ROOT),
                "dataRoot": str(self.data_root),
                "serviceDataRoot": str(self.service_data_root),
                "libraryRoot": str(self.library_root),
                "logDir": str(self.log_dir),
            },
            "sources": sources,
        }

    async def status(self) -> dict[str, Any]:
        snap = self.snapshot()
        running_children = 0
        for child in self.children:
            if await self._service_ready(child.base_url):
                running_children += 1
        return {
            "service": "SoloData",
            "status": "ok",
            "sources": snap["service"]["metrics"]["sources"],
            "childrenRunning": running_children,
            "files": snap["service"]["metrics"]["files"],
            "dataRoot": snap["paths"]["serviceDataRoot"],
            "mcp": "/mcp",
        }

    async def start_children(self) -> None:
        for child in self.children:
            if await self._service_ready(child.base_url, timeout=0.8):
                child.external = True
                print(f"  > {child.label} already running at {child.base_url}", flush=True)
                continue
            child.log_path.parent.mkdir(parents=True, exist_ok=True)
            child.log_file = child.log_path.open("a", encoding="utf-8")
            child.process = subprocess.Popen(
                child.command,
                cwd=child.cwd,
                stdout=child.log_file,
                stderr=child.log_file,
                env=os.environ.copy(),
            )
            print(f"  > {child.label} starting (pid {child.process.pid}) log -> {child.log_path}", flush=True)

        await asyncio.gather(*(self._wait_for_child(child) for child in self.children))
        self.clients = {
            "feeds": httpx.AsyncClient(base_url=self.feeds_base_url, timeout=30.0),
            "library": httpx.AsyncClient(base_url=self.library_base_url, timeout=30.0),
            "reference": httpx.AsyncClient(base_url=self.reference_base_url, timeout=30.0),
            "rag": httpx.AsyncClient(base_url=self.rag_base_url, timeout=30.0),
            "graph": httpx.AsyncClient(base_url=self.graph_base_url, timeout=30.0),
        }

    async def stop_children(self) -> None:
        for client in self.clients.values():
            await client.aclose()
        self.clients = {}
        for child in reversed(self.children):
            if child.external or child.process is None or child.process.poll() is not None:
                continue
            print(f"  < stopping {child.label} (pid {child.process.pid})", flush=True)
            child.process.terminate()
        for child in reversed(self.children):
            if child.external or child.process is None:
                continue
            try:
                child.process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                child.process.kill()
            if child.log_file is not None:
                child.log_file.close()
                child.log_file = None

    async def search(
        self,
        query: str,
        domains: list[str] | None = None,
        domain: str = "all",
        limit: int = 20,
    ) -> dict[str, Any]:
        query = query.strip()
        selected_domains = domains or ([] if domain in ("", "all") else [domain])
        if not selected_domains:
            selected_domains = [source.slug for source in self.sources]
        selected = [source for source in self.sources if source.slug in selected_domains]
        searchable = [source for source in selected if source.slug in self.clients]
        if not query:
            return {
                "query": query,
                "domains_searched": [],
                "results_by_domain": {},
                "results": [],
            }
        results_by_domain: dict[str, Any] = {}
        for source in searchable:
            try:
                results_by_domain[source.slug] = await self._search_source(source, query, limit)
            except Exception as exc:
                results_by_domain[source.slug] = {"error": str(exc)}
        return {
            "query": query,
            "domains_searched": [source.slug for source in searchable],
            "results_by_domain": results_by_domain,
            "results": flatten_search_results(results_by_domain),
        }

    async def list_library_books(self, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        response = await self._service_request("library", "GET", "/api/books", params={"limit": _bounded_limit(limit, 200, 250), "offset": max(0, offset)})
        books = response.get("books") if isinstance(response.get("books"), list) else []
        return {"count": len(books), "books": [self._library_index_item(book) for book in books]}

    async def get_library_book(self, book_id: str) -> dict[str, Any]:
        return await self._service_request("library", "GET", f"/api/books/{quote(str(book_id), safe='')}")

    async def get_library_book_chunk(self, book_id: str, offset_chars: int = 0, length_chars: int = CHUNK_SIZE) -> dict[str, Any]:
        book = await self.get_library_book(book_id)
        body = str(book.get("body") or "")
        offset = max(0, _parse_int(offset_chars, 0))
        length = max(100, min(_parse_int(length_chars, CHUNK_SIZE), 16000))
        chunk = body[offset:offset + length]
        next_offset = offset + len(chunk)
        has_more = next_offset < len(body)
        payload: dict[str, Any] = {
            "chunk": chunk,
            "offset_chars": offset,
            "next_offset": next_offset if has_more else None,
            "total_chars": len(body),
            "has_more": has_more,
        }
        if offset == 0:
            payload.update({
                "book_id": book.get("route_id") or book_id,
                "title": book.get("title"),
                "author": book.get("author"),
                "year": book.get("year"),
                "language": book.get("language"),
                "genre": book.get("genre"),
                "word_count": book.get("word_count"),
            })
        return payload

    async def find_library_book(self, title: str) -> dict[str, Any]:
        payload = await self._service_request("library", "GET", "/api/search", params={"q": title, "limit": 20})
        raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
        results = [item for item in raw_results if isinstance(item, dict)]
        query = title.lower()

        def rank(item: dict[str, Any]) -> tuple[int, str]:
            text = str(item.get("title") or "").lower()
            if text == query:
                return (0, text)
            if text.startswith(query):
                return (1, text)
            return (2, text)

        results.sort(key=rank)
        return {
            "count": len(results),
            "matches": [
                {
                    "book_id": item.get("route_id") or item.get("id"),
                    "title": item.get("title"),
                    "author": item.get("author"),
                    "year": item.get("year"),
                    "genre": item.get("genre"),
                    "word_count": item.get("word_count"),
                    "chunks": math.ceil((item.get("word_count") or 0) * 5 / CHUNK_SIZE) or None,
                }
                for item in results
            ],
        }

    async def import_library_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._service_request("library", "POST", "/api/books", json=payload)
        return response

    async def update_library_book(self, book_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._service_request("library", "PATCH", f"/api/books/{quote(str(book_id), safe='')}", json=payload)

    async def delete_library_book(self, book_id: str) -> dict[str, Any]:
        return await self._service_request("library", "DELETE", f"/api/books/{quote(str(book_id), safe='')}")

    async def full_text(self, refid: str) -> dict[str, Any]:
        kind, values = parse_artifact_ref(refid)
        if kind == "feed_entry":
            entry_id = values.get("entry_id") or values.get("id") or ""
            if not entry_id:
                return {"error": "feed_entry artifact_ref is missing entry_id"}
            entry = await self._service_request("feeds", "GET", f"/api/entries/{quote(entry_id, safe='')}")
            return {"artifact_ref": refid, "type": kind, "body": entry.get("content") or "", **entry}
        if kind == "library_book":
            book_id = values.get("book_id") or values.get("id") or ""
            if not book_id:
                return {"error": "library_book artifact_ref is missing book_id"}
            book = await self.get_library_book(book_id)
            return {
                "artifact_ref": refid,
                "type": "library_book",
                "book_id": book.get("route_id") or book_id,
                "title": book.get("title"),
                "author": book.get("author"),
                "year": book.get("year"),
                "language": book.get("language"),
                "genre": book.get("genre"),
                "notes": book.get("notes"),
                "word_count": book.get("word_count"),
                "body": book.get("body") or "",
            }
        if kind == "reference_article":
            title = values.get("title") or ""
            if not title:
                return {"error": "reference_article artifact_ref is missing title"}
            article = await self._service_request("reference", "GET", f"/articles/{quote(title, safe='')}")
            return {"artifact_ref": refid, "type": kind, **article}
        if kind == "rag_chunk":
            chunk_id = values.get("chunk_id") or values.get("id") or ""
            db = values.get("db") or "default"
            if not chunk_id:
                return {"error": "rag_chunk artifact_ref is missing chunk_id"}
            chunk = await self._service_request("rag", "GET", f"/chunks/{quote(chunk_id, safe='')}", params={"db": db})
            return {"artifact_ref": refid, "type": kind, "body": chunk.get("content") or "", **chunk}
        if kind == "graph_connection":
            return {"artifact_ref": refid, "type": kind, "detail": "Graph connections are returned in search results; use graph expansion tools for neighbourhoods."}
        return {"error": f"Unsupported artifact_ref kind: {kind or '<empty>'}"}

    async def _search_source(self, source: ServiceEndpoint, query: str, limit: int) -> list[dict[str, Any]]:
        payload = await self._service_request(source.slug, "GET", "/api/search", params={"q": query, "limit": _bounded_limit(limit)})
        raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
        if source.slug == "feeds":
            return [map_feed_result(item, source.ui_url) for item in raw_results if isinstance(item, dict)]
        if source.slug == "library":
            return [map_library_result(item, source.ui_url) for item in raw_results if isinstance(item, dict)]
        if source.slug == "reference":
            return [map_reference_result(item, source.ui_url) for item in raw_results if isinstance(item, dict)]
        if source.slug == "rag":
            return [map_rag_result(item, source.ui_url) for item in raw_results if isinstance(item, dict)]
        if source.slug == "graph":
            return [map_graph_result(item, source.ui_url) for item in raw_results if isinstance(item, dict)]
        return []

    async def _search_library(self, query: str, limit: int) -> list[dict[str, Any]]:
        payload = await self._service_request("library", "GET", "/api/search", params={"q": query, "limit": _bounded_limit(limit)})
        raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
        return [map_library_result(item, f"{self.library_base_url}/ui") for item in raw_results if isinstance(item, dict)]

    async def _service_request(
        self,
        slug: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self.clients.get(slug)
        if client is None:
            raise HTTPException(status_code=503, detail=f"{slug} is not ready")
        response = await client.request(method, path, params=params, json=json)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"{slug} item not found")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise HTTPException(status_code=response.status_code, detail=detail or f"{slug} HTTP {response.status_code}")
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}

    async def _service_ready(self, base_url: str, timeout: float = 2.0) -> bool:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
                response = await client.get("/status")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def _wait_for_child(self, child: ChildProcess, timeout: float = 30.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if await self._service_ready(child.base_url, timeout=1.0):
                print(f"  > {child.label} ready at {child.base_url}", flush=True)
                return
            await asyncio.sleep(0.4)
        print(f"  ! {child.label} did not become ready within {timeout:.0f}s", flush=True)

    def _source_snapshot(self, source: ServiceEndpoint) -> dict[str, Any]:
        files = [path for path in source.folder.rglob("*") if path.is_file()] if source.folder.exists() else []
        running = False
        child = next((item for item in self.children if item.base_url == source.base_url), None)
        if child is not None:
            running = child.external or (child.process is not None and child.process.poll() is None)
        return {
            "slug": source.slug,
            "label": source.label,
            "description": source.description,
            "path": str(source.folder),
            "uiUrl": source.ui_url,
            "searchUrl": source.search_url,
            "running": running,
            "exists": source.folder.exists(),
            "fileCount": len(files),
            "updated": self._latest_mtime(files),
        }

    @staticmethod
    def _latest_mtime(files: list[Path]) -> str:
        if not files:
            return ""
        latest = max(path.stat().st_mtime for path in files)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest))

    @staticmethod
    def _library_index_item(book: dict[str, Any]) -> dict[str, Any]:
        word_count = book.get("word_count") or 0
        return {
            "book_id": book.get("route_id") or book.get("id"),
            "title": book.get("title"),
            "author": book.get("author"),
            "year": book.get("year"),
            "catalog": book.get("catalog"),
            "genre": book.get("genre"),
            "word_count": word_count,
            "chunks": math.ceil(word_count * 5 / CHUNK_SIZE) or None,
        }


config = load_config()
gateway = SoloDataGateway(config)

_mcp = FastMCP(
    "SoloDataGateway",
    instructions=(
        "SoloDataGateway provides search and edit tools for SoloData services. "
        "Use solodata_search to find records. Search results may include artifact_ref; "
        "pass those to solodata_get_full_text for complete source text. "
        "SoloLibrary books are long, so prefer solodata_get_library_book_chunk for reading. "
        "Use feeds for current/saved entries, reference for articles, rag for chunks, and graph for concept relations."
    ),
    streamable_http_path="/",
    stateless_http=True,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print("\nSoloDataGateway: starting child services", flush=True)
    await gateway.start_children()
    async with _mcp.session_manager.run():
        yield
    print("\nSoloDataGateway: shutting down child services", flush=True)
    await gateway.stop_children()


app = FastAPI(
    title="SoloDataGateway",
    description="SoloData web gateway and MCP server",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
@app.get("/ui", include_in_schema=False)
async def ui_root() -> FileResponse:
    return file_response(UI_DIR / "index.html")


@app.get("/ui/{asset_path:path}", include_in_schema=False)
async def ui_asset(asset_path: str) -> FileResponse:
    if "." not in Path(asset_path).name:
        return file_response(UI_DIR / "index.html")
    return bounded_file_response(UI_DIR, asset_path)


@app.get("/common/{asset_path:path}", include_in_schema=False)
async def common_asset(asset_path: str) -> FileResponse:
    return bounded_file_response(COMMON_UI_DIR, asset_path)


@app.get("/status")
async def api_status() -> dict[str, Any]:
    return await gateway.status()


@app.get("/api/snapshot")
async def api_snapshot() -> dict[str, Any]:
    return gateway.snapshot()


@app.get("/api/search")
async def api_search_get(q: str = "", domain: str = "all", limit: int = 20) -> dict[str, Any]:
    return await gateway.search(q, domain=domain, limit=limit)


@app.post("/api/search")
async def api_search_post(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        payload = {}
    domains = payload.get("domains")
    selected_domains = [str(item) for item in domains] if isinstance(domains, list) else None
    return await gateway.search(
        str(payload.get("query") or payload.get("q") or ""),
        selected_domains,
        limit=_bounded_limit(payload.get("limit") or 20),
    )


@app.post("/api/full-text")
async def api_full_text(request: Request) -> dict[str, Any]:
    payload = await request.json()
    refid = str(payload.get("refid") or payload.get("artifact_ref") or "") if isinstance(payload, dict) else ""
    return await gateway.full_text(refid)


@_mcp.tool()
async def solodata_search(query: str, domains: list[str] | None = None, limit: int = 20) -> dict:
    """Search across running SoloData services.

    Args:
        query: Search string. Empty queries return no results.
        domains: Optional list of service domains. Currently "library" is implemented.
        limit: Maximum results per selected domain.

    Returns:
        Structured results with snippet, URL, and artifact_ref fields where available.
    """
    return await gateway.search(query, domains=domains, limit=limit)


@_mcp.tool()
async def solodata_get_full_text(refid: str) -> dict:
    """Fetch complete text for a search result artifact_ref."""
    return await gateway.full_text(refid)


@_mcp.tool()
async def solodata_get_feed_entry(entry_id: int) -> dict:
    """Fetch a full SoloFeeds entry by numeric ID."""
    return await gateway._service_request("feeds", "GET", f"/api/entries/{entry_id}")


@_mcp.tool()
async def solodata_add_feed_entry(title: str, content: str, url: str | None = None, domain: str = "default", summary: str | None = None) -> dict:
    """Add a saved feed-style entry."""
    return await gateway._service_request(
        "feeds",
        "POST",
        "/api/entries",
        json={"title": title, "content": content, "url": url, "domain": domain, "summary": summary},
    )


@_mcp.tool()
async def solodata_find_library_book(title: str) -> dict:
    """Find library books by title or partial title."""
    return await gateway.find_library_book(title)


@_mcp.tool()
async def solodata_get_library_index(limit: int = 200, offset: int = 0) -> dict:
    """Return an index of library books with IDs and chunk counts."""
    return await gateway.list_library_books(limit=limit, offset=offset)


@_mcp.tool()
async def solodata_get_library_book_chunk(book_id: str, offset_chars: int = 0, length_chars: int = CHUNK_SIZE) -> dict:
    """Read a library book in bounded text chunks."""
    return await gateway.get_library_book_chunk(book_id, offset_chars=offset_chars, length_chars=length_chars)


@_mcp.tool()
async def solodata_import_library_book(
    title: str,
    body: str,
    author: str | None = None,
    year: int | None = None,
    language: str | None = None,
    genre: str | None = None,
    notes: str | None = None,
    source: str | None = None,
    source_id: str | None = None,
    catalog: str | None = None,
) -> dict:
    """Import a new book into SoloLibrary."""
    payload = {
        "title": title,
        "body": body,
        "author": author,
        "year": year,
        "language": language,
        "genre": genre,
        "notes": notes,
        "source": source,
        "source_id": source_id,
        "catalog": catalog,
    }
    return await gateway.import_library_book({key: value for key, value in payload.items() if value is not None})


@_mcp.tool()
async def solodata_update_library_book(book_id: str, updates: dict) -> dict:
    """Update an existing SoloLibrary book.

    updates may contain title, author, year, language, genre, notes, source, source_id, or body.
    """
    return await gateway.update_library_book(book_id, updates if isinstance(updates, dict) else {})


@_mcp.tool()
async def solodata_delete_library_book(book_id: str) -> dict:
    """Delete a SoloLibrary book by book_id."""
    return await gateway.delete_library_book(book_id)


@_mcp.tool()
async def solodata_get_reference_article(title: str) -> dict:
    """Fetch a full SoloReference article by title."""
    return await gateway._service_request("reference", "GET", f"/articles/{quote(title, safe='')}")


@_mcp.tool()
async def solodata_upsert_reference_article(title: str, body: str, summary: str | None = None, source: str | None = None, source_id: str | None = None) -> dict:
    """Add or update a SoloReference article."""
    return await gateway._service_request(
        "reference",
        "POST",
        "/articles",
        json={"title": title, "body": body, "summary": summary, "source": source, "source_id": source_id},
    )


@_mcp.tool()
async def solodata_get_rag_chunk(chunk_id: int, db: str = "default") -> dict:
    """Fetch a full SoloRAG chunk."""
    return await gateway._service_request("rag", "GET", f"/chunks/{chunk_id}", params={"db": db})


@_mcp.tool()
async def solodata_add_rag_chunk(title: str, content: str, db: str = "default", source: str | None = None, tags: list[str] | None = None, metadata: dict | None = None) -> dict:
    """Add a chunk to a SoloRAG database."""
    return await gateway._service_request(
        "rag",
        "POST",
        "/chunks",
        params={"db": db},
        json={"title": title, "content": content, "source": source, "tags": tags or [], "metadata": metadata or {}},
    )


@_mcp.tool()
async def solodata_create_graph_connection(start: str, connection: str, end: str, evidence: str | None = None, score: float = 1.0) -> dict:
    """Create or reinforce a SoloGraph connection by names."""
    return await gateway._service_request(
        "graph",
        "POST",
        "/api/connections/by-name",
        json={"start": start, "connection": connection, "end": end, "evidence": evidence, "score": score},
    )


@_mcp.tool()
async def solodata_create_graph_connections(connections: list[dict]) -> dict:
    """Create or reinforce several SoloGraph connections by names."""
    return await gateway._service_request("graph", "POST", "/api/connections/by-name/batch", json={"connections": connections})


@_mcp.tool()
async def solodata_expand_graph_term(term: str, depth: int = 1, limit: int = 50) -> dict:
    """Return a small SoloGraph neighbourhood around a term."""
    return await gateway._service_request("graph", "GET", "/api/expand-by-term", params={"term": term, "depth": depth, "limit": limit})


app.mount("/mcp", _mcp.streamable_http_app())


def bounded_file_response(root: Path, relative_path: str) -> FileResponse:
    target = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(status_code=404, detail="File not found")
    return file_response(target)


def file_response(path: Path) -> FileResponse:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), headers={"Cache-Control": "no-store"})


def print_status(host: str, port: int) -> None:
    snapshot = gateway.snapshot()
    print("SoloDataGateway status")
    print(f"  url        http://{host}:{port}/")
    print(f"  mcp        http://{host}:{port}/mcp")
    print(f"  data       {snapshot['paths']['serviceDataRoot']}")
    for source in gateway.sources:
        print(f"  {source.slug:<10} {source.base_url}")


def main() -> int:
    args = parse_args()
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    solodata = services.get("solodata") if isinstance(services.get("solodata"), dict) else {}
    host = args.host or str(solodata.get("host") or network.get("host") or "127.0.0.1")
    port = int(args.port or solodata.get("port") or 9740)
    if args.command == "status" or args.dry_run:
        print_status(host, port)
        return 0
    print(f"SoloDataGateway: http://{host}:{port}/ui", flush=True)
    print(f"SoloDataGateway MCP: http://{host}:{port}/mcp", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
