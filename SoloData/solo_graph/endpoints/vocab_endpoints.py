# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# HTTP endpoint handlers for SoloGraph vocab operations.

from __future__ import annotations

from typing import Any

from common_utils.service import query_int, query_text


# ---------------------------------------------------------------------------
# MARK: List
# ---------------------------------------------------------------------------

def list_vocab(
    store: Any,
    params: dict[str, list[str]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "vocab": store.list_vocab(
            q=query_text(params, "q"),
            limit=query_int(params, "limit", 100),
            offset=query_int(params, "offset", 0),
        )
    }


# ---------------------------------------------------------------------------
# MARK: Get
# ---------------------------------------------------------------------------

def get_vocab(
    store: Any,
    params: dict[str, list[str]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    vocab_id = int(query_text(params, "id") or 0)

    item = store.get_vocab(vocab_id)

    if item is None:
        raise ValueError(f"Vocab item not found: {vocab_id}")

    return item


# ---------------------------------------------------------------------------
# MARK: Add
# ---------------------------------------------------------------------------

def add_vocab(
    store: Any,
    params: dict[str, list[str]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return store.add_vocab(payload)


# ---------------------------------------------------------------------------
# MARK: Update
# ---------------------------------------------------------------------------

def update_vocab(
    store: Any,
    params: dict[str, list[str]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    vocab_id = int(query_text(params, "id") or 0)

    item = store.update_vocab(vocab_id, payload)

    if item is None:
        raise ValueError(f"Vocab item not found: {vocab_id}")

    return item


# ---------------------------------------------------------------------------
# MARK: Delete
# ---------------------------------------------------------------------------

def delete_vocab(
    store: Any,
    params: dict[str, list[str]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    vocab_id = int(query_text(params, "id") or 0)

    ok = store.delete_vocab(vocab_id)

    return {
        "ok": ok,
        "id": vocab_id,
    }