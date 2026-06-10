// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Central entry point for the SoloGraph page.
// Owns state, render, and shared fetch utilities.
// Sub-modules (graph-vocab, graph-connections, graph-import-export) import from here.

import { createHeading, createPage, createParagraph } from "/common/framework/js/basic-layout.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import * as layout from "/common/framework/js/layout-components.js";
import { loadVocab, createGraphVocab } from "./graph-vocab.js";
import { loadConnections, createGraphConnections } from "./graph-connections.js";
import { exportCsvFiles, importCsvFiles } from "./graph-import-export.js";

const mount = document.querySelector("#app");

export const state = {
    pageSpec: null,

    vocab: [],
    vocabHasMore: false,
    vocabOffset: 0,
    vocabLimit: 50,
    addVocabTerm: "",
    editingVocabId: null,
    editingVocabTerm: "",

    connections: [],
    connHasMore: false,
    connOffset: 0,
    connLimit: 100,
    connSearch: "",
    addStart: "",
    addVia: "",
    addEnd: "",
};

const controlFactories = {
    graphConnections: createGraphConnections,
    graphVocab: createGraphVocab,
    heading: createHeading,
    iconTextButton: createActionIconTextButton,
    paragraph: createParagraph,
    textLabel: createTextLabel,
};

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
    state.pageSpec = await fetchJson("/ui/page.json");

    await Promise.all([
        loadVocab(),
        loadConnections(),
    ]);

    render();
}

export function render() {
    mount.replaceChildren(createPage(state.pageSpec, createControl));
}

function createControl(spec) {
    const factory = controlFactories[spec.type];

    if (!factory) {
        throw new Error(`Unknown component type: ${spec.type}`);
    }

    return factory(spec);
}

// ---------------------------------------------------------------------------
// Top actions
// ---------------------------------------------------------------------------

function createActionIconTextButton(spec) {
    const button = createIconTextButton(spec);

    if (spec.action === "refresh") {
        button.addEventListener("click", async () => {
            await Promise.all([
                loadVocab(),
                loadConnections(),
            ]);

            render();
        });
    }

    if (spec.action === "export") {
        button.addEventListener("click", exportCsvFiles);
    }

    if (spec.action === "import") {
        button.addEventListener("click", importCsvFiles);
    }

    return button;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

export async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const text = await response.text();

    if (!response.ok) {
        throw new Error(formatHttpError(url, response.status, text));
    }

    if (!text.trim()) {
        return {};
    }

    try {
        return JSON.parse(text);
    } catch {
        throw new Error(`${url} returned invalid JSON: ${trimForAlert(text)}`);
    }
}

export async function fetchOk(url, options = {}) {
    const response = await fetch(url, options);
    const text = await response.text();

    if (!response.ok) {
        throw new Error(formatHttpError(url, response.status, text));
    }
}

function formatHttpError(url, status, text) {
    const detail = extractUsefulErrorText(text);

    if (!detail) {
        return `${url} returned HTTP ${status}`;
    }

    return `${url} returned HTTP ${status}: ${detail}`;
}

function extractUsefulErrorText(text) {
    if (!text) {
        return "";
    }

    const trimmed = text.trim();

    if (!trimmed) {
        return "";
    }

    if (trimmed.startsWith("<!DOCTYPE") || trimmed.startsWith("<html")) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(trimmed, "text/html");

        const title = doc.querySelector("title")?.textContent?.trim();
        const body = doc.body?.textContent?.trim();

        return trimForAlert(body || title || "HTML error response");
    }

    try {
        const obj = JSON.parse(trimmed);

        if (obj.error) {
            return String(obj.error);
        }

        if (obj.message) {
            return String(obj.message);
        }

        return trimForAlert(JSON.stringify(obj));
    } catch {
        return trimForAlert(trimmed);
    }
}

function trimForAlert(text, maxLen = 500) {
    const singleLine = String(text)
        .replace(/\s+/g, " ")
        .trim();

    if (singleLine.length <= maxLen) {
        return singleLine;
    }

    return `${singleLine.slice(0, maxLen)}...`;
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

boot().catch((error) => {
    mount.replaceChildren(layout.shell([
        layout.errorText(error.message),
    ]));
});