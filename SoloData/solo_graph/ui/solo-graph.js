// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders the SoloGraph page from ui/page.json and service API responses.

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
    graphConnections: createGraphConnections,
    graphPaths: createGraphPaths,
    graphSearch: createGraphSearch,
    graphStatus: createGraphStatus,
    graphVocab: createGraphVocab,
    heading: createHeading,
    iconTextButton: createActionIconTextButton,
    paragraph: createParagraph,
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

function createGraphStatus() {
    const metrics = snapshot?.metrics ?? {};
    return layout.metricGrid([
        ["Vocab", metrics.vocab ?? 0],
        ["Relations", metrics.relations ?? 0],
        ["Uptime", `${snapshot?.uptimeSec ?? 0}s`]
    ]);
}

function createGraphPaths() {
    const paths = snapshot?.paths ?? {};
    return layout.pathList([
        ["Graph", paths.graphRoot ?? ""],
        ["Database", paths.db ?? ""],
        ["Logs", paths.logs ?? ""]
    ]);
}

function createGraphSearch() {
    const input = createLineEdit({ value: query, placeholder: "Search concepts and connections" });
    input.addEventListener("input", () => {
        query = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            runSearch();
        }
    });
    const button = createTextButton({ text: "Search", color: "#8bd5ff" });
    button.addEventListener("click", runSearch);
    return layout.stack([
        layout.searchRow([input, button], { singleAction: true }),
        results.length ? createConnectionList(results) : layout.mutedText(query ? "No results." : "Results appear here after a search.")
    ]);
}

async function runSearch() {
    const data = await fetchJson(`/api/search?q=${encodeURIComponent(query)}&limit=25`);
    results = data.results ?? [];
    render();
}

function createGraphConnections() {
    return createConnectionList(snapshot?.recentConnections ?? []);
}

function createConnectionList(items) {
    if (!items.length) {
        return layout.mutedText("No connections yet.");
    }
    return layout.itemList(items.map((item) => layout.item([
        layout.titleRow(`${item.start ?? ""} ${item.predicate ?? ""} ${item.end ?? ""}`, String(item.score ?? "")),
        layout.normalText(item.evidence || ""),
        layout.mutedText([item.state, item.updated_at].filter(Boolean).join(" | "))
    ])));
}

function createGraphVocab() {
    const items = snapshot?.recentVocab ?? [];
    if (!items.length) {
        return layout.mutedText("No vocab yet.");
    }
    return layout.itemList(items.map((item) => layout.item([
        layout.titleRow(item.term ?? "", item.kind ?? ""),
        layout.normalText(item.notes || "")
    ])));
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([layout.errorText(error.message)]));
});
