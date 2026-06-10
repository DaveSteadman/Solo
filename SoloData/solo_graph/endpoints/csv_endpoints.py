from __future__ import annotations

from typing import Any

import csv_io


def export_connections(store: Any, params: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, Any]:
    csv_path = store.graph_root / "connections.csv"
    exported = csv_io.export_connections(store.db_path, csv_path)

    return {
        "exported": exported,
        "file": str(csv_path),
    }


def import_connections(store: Any, params: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, Any]:
    return csv_io.import_connections(
        store.db_path,
        store.graph_root / "connections.csv",
    )