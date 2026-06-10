// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// CSV import and export actions for SoloGraph.

import { fetchJson, render } from "./solo-graph.js";
import { loadVocab } from "./graph-vocab.js";
import { loadConnections } from "./graph-connections.js";

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export async function exportCsvFiles() {
    try {
        const result = await fetchJson("/api/export/connections");

        alert(`Exported ${result.exported} connections → ${result.file}`);
    } catch (err) {
        alert(`Export failed: ${err.message}`);
    }
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

export async function importCsvFiles() {
    try {
        const result = await fetchJson("/api/import/connections", { method: "POST" });

        await Promise.all([
            loadVocab(),
            loadConnections(),
        ]);

        render();

        const connError = result.error ? `\n(${result.error})` : "";

        alert(
            `Imported connections: ${result.imported ?? 0} new, ${result.skipped ?? 0} skipped.${connError}`
        );
    } catch (err) {
        alert(`Import failed: ${err.message}`);
    }
}
