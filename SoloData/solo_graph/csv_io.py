# ---------------------------------------------------------------------------
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# CSV import and export for SoloGraph connections.
# connections.csv — columns: start, connection, end
# ---------------------------------------------------------------------------

from __future__ import annotations

import csv
from pathlib import Path

from common_utils.sqlite import sqlite_connection
import database


# ---------------------------------------------------------------------------
# MARK: Export
# ---------------------------------------------------------------------------

def export_connections(db_path: Path, csv_path: Path) -> int:
    """Write all graph connections to one CSV file. Returns row count written."""
    with sqlite_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.term AS start, p.term AS connection, e.term AS end
            FROM connections c
            JOIN vocab s ON s.id = c.start_id
            JOIN vocab p ON p.id = c.connection_id
            JOIN vocab e ON e.id = c.end_id
            ORDER BY s.term COLLATE NOCASE,
                     p.term COLLATE NOCASE,
                     e.term COLLATE NOCASE
            """
        ).fetchall()

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["start", "connection", "end"])

        for row in rows:
            writer.writerow([row["start"], row["connection"], row["end"]])

    return len(rows)


# ---------------------------------------------------------------------------
# MARK: Import
# ---------------------------------------------------------------------------

def import_connections(db_path: Path, csv_path: Path) -> dict[str, int | str]:
    """
    Import graph connections from one CSV file.

    Creates missing vocab terms automatically.
    Skips blank rows, incomplete rows, and exact duplicate connections.

    Returns:
        {
            "imported": N,
            "skipped": N
        }
    """
    if not csv_path.exists():
        return {
            "imported": 0,
            "skipped":  0,
            "error":    f"File not found: {csv_path}",
        }

    imported = 0
    skipped  = 0

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader   = csv.DictReader(f)
        required = {"start", "connection", "end"}

        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                "connections.csv must have 'start', 'connection' and 'end' column headers"
            )

        rows = list(reader)

    with sqlite_connection(db_path) as conn:
        for row in rows:
            start = str(row.get("start") or "").strip()
            via   = str(row.get("connection") or "").strip()
            end   = str(row.get("end") or "").strip()

            if not start or not via or not end:
                skipped += 1
                continue

            start_id = database.get_or_create_vocab(conn, start)
            via_id   = database.get_or_create_vocab(conn, via)
            end_id   = database.get_or_create_vocab(conn, end)

            existing = conn.execute(
                """
                SELECT id
                FROM connections
                WHERE start_id = ?
                  AND connection_id = ?
                  AND end_id = ?
                """,
                (start_id, via_id, end_id),
            ).fetchone()

            if existing:
                skipped += 1
                continue

            conn.execute(
                """
                INSERT INTO connections(start_id, connection_id, end_id)
                VALUES (?, ?, ?)
                """,
                (start_id, via_id, end_id),
            )

            imported += 1

    return {
        "imported": imported,
        "skipped":  skipped,
    }


