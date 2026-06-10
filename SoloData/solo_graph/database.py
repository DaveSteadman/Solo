# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Database creation and low-level access for SoloGraph.
# All functions take an open sqlite connection so they compose inside transactions.
# The caller (SoloGraphStore in main.py) is responsible for opening the connection.

from __future__ import annotations

from pathlib import Path
from typing import Any

from common_utils.sqlite import sqlite_connection


_DEFAULT_PREDICATES = (
    "is_a", "part_of", "contributed_to", "discovered", "developed",
    "proposed", "invented", "studied", "applied_to", "influenced",
    "precedes", "lived_in", "wrote", "disproved", "succeeded", "is_type_of",
)


# ---------------------------------------------------------------------------
# MARK: Create
# ---------------------------------------------------------------------------

def create_database(db_path: Path) -> None:
    """Create tables and seed default predicates into vocab."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vocab (
                id   INTEGER PRIMARY KEY,
                term TEXT NOT NULL UNIQUE COLLATE NOCASE
            );

            CREATE TABLE IF NOT EXISTS connections (
                id            INTEGER PRIMARY KEY,
                start_id      INTEGER NOT NULL REFERENCES vocab(id) ON DELETE CASCADE,
                connection_id INTEGER NOT NULL REFERENCES vocab(id) ON DELETE CASCADE,
                end_id        INTEGER NOT NULL REFERENCES vocab(id) ON DELETE CASCADE,
                UNIQUE(start_id, connection_id, end_id)
            );
            """
        )
        for predicate in _DEFAULT_PREDICATES:
            get_or_create_vocab(conn, predicate)


# ---------------------------------------------------------------------------
# MARK: Vocab
# ---------------------------------------------------------------------------

def list_vocab(conn: Any, q: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    where = "WHERE term LIKE ?" if q else ""
    params: list[Any] = [f"%{q}%"] if q else []
    params.extend([max(1, min(limit, 250)), max(0, offset)])
    rows = conn.execute(
        f"SELECT id, term FROM vocab {where} ORDER BY term LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_vocab(conn: Any, vocab_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, term FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
    return dict(row) if row else None


def add_vocab(conn: Any, term: str) -> dict[str, Any]:
    clean = str(term or "").strip()
    if not clean:
        raise ValueError("term is required")
    vocab_id = get_or_create_vocab(conn, clean)
    return dict(conn.execute("SELECT id, term FROM vocab WHERE id = ?", (vocab_id,)).fetchone())


def update_vocab(conn: Any, vocab_id: int, term: str) -> dict[str, Any] | None:
    clean = str(term or "").strip()
    if not clean:
        raise ValueError("term is required")
    conn.execute("UPDATE vocab SET term = ? WHERE id = ?", (clean, vocab_id))
    row = conn.execute("SELECT id, term FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
    return dict(row) if row else None


def delete_vocab(conn: Any, vocab_id: int) -> bool:
    return conn.execute("DELETE FROM vocab WHERE id = ?", (vocab_id,)).rowcount > 0


def get_or_create_vocab(conn: Any, term: str) -> int:
    clean = str(term or "").strip()
    row = conn.execute("SELECT id FROM vocab WHERE term = ? COLLATE NOCASE", (clean,)).fetchone()
    if row:
        return int(row["id"])
    return int(conn.execute("INSERT INTO vocab(term) VALUES (?)", (clean,)).lastrowid)


# ---------------------------------------------------------------------------
# MARK: Connections
# ---------------------------------------------------------------------------

_CONNECTION_SELECT = """
    SELECT c.id, c.start_id, s.term AS start,
           c.connection_id, p.term AS connection,
           c.end_id, e.term AS end
    FROM connections c
    JOIN vocab s ON s.id = c.start_id
    JOIN vocab p ON p.id = c.connection_id
    JOIN vocab e ON e.id = c.end_id
"""


def list_connections(conn: Any, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"{_CONNECTION_SELECT} ORDER BY c.id DESC LIMIT ? OFFSET ?",
        [max(1, min(limit, 250)), max(0, offset)],
    ).fetchall()
    return [dict(row) for row in rows]


def add_connection(conn: Any, start_id: int, connection_id: int, end_id: int) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO connections(start_id, connection_id, end_id) VALUES (?, ?, ?)"
        " ON CONFLICT(start_id, connection_id, end_id) DO NOTHING",
        (start_id, connection_id, end_id),
    )
    row = conn.execute(
        f"{_CONNECTION_SELECT} WHERE c.start_id = ? AND c.connection_id = ? AND c.end_id = ?",
        (start_id, connection_id, end_id),
    ).fetchone()
    return dict(row)


def add_connection_by_name(conn: Any, start: str, connection: str, end: str) -> dict[str, Any]:
    return add_connection(
        conn,
        get_or_create_vocab(conn, start),
        get_or_create_vocab(conn, connection),
        get_or_create_vocab(conn, end),
    )


def delete_connection(conn: Any, connection_id: int) -> bool:
    return conn.execute("DELETE FROM connections WHERE id = ?", (connection_id,)).rowcount > 0


def search_connections(conn: Any, q: str, limit: int = 20) -> list[dict[str, Any]]:
    q = str(q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = conn.execute(
        f"{_CONNECTION_SELECT}"
        " WHERE s.term LIKE ? OR p.term LIKE ? OR e.term LIKE ?"
        " ORDER BY c.id DESC LIMIT ?",
        (like, like, like, max(1, min(limit, 200))),
    ).fetchall()
    return [dict(row) for row in rows]
