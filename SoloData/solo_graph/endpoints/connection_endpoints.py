from __future__ import annotations

from typing import Any

from common_utils.service import query_int, query_text


def list_connections(store: Any, params: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "connections": store.list_connections(
            limit=query_int(params, "limit", 100),
            offset=query_int(params, "offset", 0),
        )
    }


def add_connection(store: Any, params: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, Any]:
    return store.add_connection_by_name(payload)


def add_connections_batch(store: Any, params: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, Any]:
    return store.add_connections_batch(payload)


def delete_connection(store: Any, params: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, Any]:
    connection_id = int(payload.get("id") or query_text(params, "id") or 0)
    ok = store.delete_connection(connection_id)
    return {"ok": ok}