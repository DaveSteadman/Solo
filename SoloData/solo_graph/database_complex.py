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

# ---------------------------------------------------------------------------
# MARK: Fully Delete
# ---------------------------------------------------------------------------

# Delete a vocab term and all connections that reference it. Use with caution!
def delete_vocab_and_connections(conn: Any, vocab_id: int) -> None:
    conn.execute("DELETE FROM vocab WHERE id = ?", (vocab_id,))

# ---------------------------------------------------------------------------
# MARK: Renumber Compact
# ---------------------------------------------------------------------------

# Renumber the vocab table to be compact and sequential starting at 1, and update all connections to match.
# Use with caution! This is not thread-safe and will invalidate any existing vocab_id values.   

def renumber_vocab_compact(conn: Any) -> None:
    # Get all vocab terms ordered by id, assign new sequential ids, and build a mapping of old_id -> new_id.
    rows = conn.execute("SELECT id, term FROM vocab ORDER BY id").fetchall()
    id_mapping = {old_id: new_id for new_id, (old_id, _) in enumerate(rows, start=1)}
    # Update vocab table with new ids.
    for old_id, term in rows:
        new_id = id_mapping[old_id]
        if new_id != old_id:
            conn.execute("UPDATE vocab SET id = ? WHERE id = ?", (new_id, old_id))
    # Update connections table with new ids.
    for old_start, old_connection, old_end in conn.execute("SELECT start_id, connection_id, end_id FROM connections").fetchall():
        new_start = id_mapping[old_start]
        new_connection = id_mapping[old_connection]
        new_end = id_mapping[old_end]
        if (new_start, new_connection, new_end) != (old_start, old_connection, old_end):
            conn.execute(
                "UPDATE connections SET start_id = ?, connection_id = ?, end_id = ? WHERE start_id = ? AND connection_id = ? AND end_id = ?",
                (new_start, new_connection, new_end, old_start, old_connection, old_end)
            )

# ---------------------------------------------------------------------------
# MARK: Connections To Depth
# ---------------------------------------------------------------------------

# when given a vocab item, find all its connections, and all connections of those connections, up to a given depth. 
# Returns a set of connection ids that are reachable within the given depth.

def find_connections_to_depth(conn: Any, vocab_id: int, depth: int) -> set[int]:
    visited = set()
    frontier = {vocab_id}
    for _ in range(depth):
        next_frontier = set()
        for current_id in frontier:
            if current_id not in visited:
                visited.add(current_id)
                rows = conn.execute(
                    "SELECT end_id FROM connections WHERE start_id = ?", (current_id,)
                ).fetchall()
                next_frontier.update(end_id for (end_id,) in rows)
        frontier = next_frontier
    return visited
