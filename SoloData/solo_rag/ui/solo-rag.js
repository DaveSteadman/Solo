// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders the SoloRAG page from ui/page.json and service API responses.

import { createHeading, createPage, createParagraph } from "/common/framework/js/basic-layout.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
let pageSpec = null;
let snapshot = null;
let query = "";
let results = [];

const controlFactories = {
    heading: createHeading,
    iconTextButton: createActionIconTextButton,
    paragraph: createParagraph,
    ragChunks: createRagChunks,
    ragDatabases: createRagDatabases,
    ragPaths: createRagPaths,
    ragSearch: createRagSearch,
    ragStatus: createRagStatus,
    textLabel: createTextLabel
};

async function boot() {
    pageSpec = await fetchJson("/ui/page.json");
    snapshot = await fetchJson("/api/snapshot");
    render();
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw new Error(`${url} returned HTTP ${response.status}`);
    }
    return response.json();
}

function render() {
    mount.replaceChildren(createPage(pageSpec, createControl));
}

function createControl(spec) {
    const factory = controlFactories[spec.type];
    if (!factory) {
        throw new Error(`Unknown component type: ${spec.type}`);
    }
    return factory(spec);
}

function createActionIconTextButton(spec) {
    const button = createIconTextButton(spec);
    if (spec.action === "refresh") {
        button.addEventListener("click", refresh);
    }
    return button;
}

async function refresh() {
    snapshot = await fetchJson("/api/snapshot");
    render();
}

function createRagStatus() {
    const metrics = snapshot?.metrics ?? {};
    return layout.metricGrid([
        ["Databases", metrics.databases ?? 0],
        ["Chunks", metrics.chunks ?? 0],
        ["Uptime", `${snapshot?.uptimeSec ?? 0}s`]
    ]);
}

function createRagPaths() {
    const paths = snapshot?.paths ?? {};
    return layout.pathList([
        ["RAG", paths.ragRoot ?? ""],
        ["Databases", paths.databases ?? ""],
        ["Logs", paths.logs ?? ""]
    ]);
}

function createRagSearch() {
    const input = createLineEdit({ value: query, placeholder: "Search chunks" });
    input.addEventListener("input", () => {
        query = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            runSearch();
        }
    });
    const button = createTextButton({ text: "Search", color: "#c4a7ff" });
    button.addEventListener("click", runSearch);
    return layout.stack([
        layout.searchRow([input, button], { singleAction: true }),
        results.length ? createChunkList(results) : layout.mutedText(query ? "No results." : "Results appear here after a search.")
    ]);
}

async function runSearch() {
    const data = await fetchJson(`/api/search?q=${encodeURIComponent(query)}&limit=25`);
    results = data.results ?? [];
    render();
}

function createRagDatabases() {
    const databases = snapshot?.databases ?? [];
    if (!databases.length) {
        return layout.mutedText("No databases yet.");
    }
    return layout.itemList(databases.map((item) => layout.item([
        layout.titleRow(item.name ?? "", `${item.chunks ?? 0} chunks`),
        layout.codeText(item.path ?? "")
    ])));
}

function createRagChunks() {
    return createChunkList(snapshot?.recentChunks ?? []);
}

function createChunkList(items) {
    if (!items.length) {
        return layout.mutedText("No chunks yet.");
    }
    return layout.itemList(items.map((item) => layout.item([
        layout.titleRow(item.title ?? "", item.db ?? ""),
        layout.normalText(item.snippet || ""),
        layout.mutedText([item.source, (item.tags ?? []).join(", ")].filter(Boolean).join(" | "))
    ])));
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([layout.errorText(error.message)]));
});
