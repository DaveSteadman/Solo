// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Connections API calls and connections UI components for SoloGraph.

import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { el } from "/common/framework/js/dom.js";
import * as layout from "/common/framework/js/layout-components.js";
import { state, render, fetchJson, fetchOk } from "./solo-graph.js";

// ---------------------------------------------------------------------------
// Connections API
// ---------------------------------------------------------------------------

export async function loadConnections(append = false) {
    const offset = append ? state.connOffset : 0;
    const query = state.connSearch.trim();

    const url = query
        ? `/api/search?q=${encodeURIComponent(query)}&limit=${state.connLimit + 1}`
        : `/api/connections?limit=${state.connLimit + 1}&offset=${offset}`;

    const data = await fetchJson(url);

    const items = query
        ? (data.results ?? [])
        : (data.connections ?? []);

    const page = items.slice(0, state.connLimit);

    state.connHasMore = !query && items.length > state.connLimit;
    state.connections = append ? [...state.connections, ...page] : page;
    state.connOffset = query ? 0 : offset + page.length;
}

async function addConnection() {
    const start = state.addStart.trim();
    const via = state.addVia.trim();
    const end = state.addEnd.trim();

    if (!start || !via || !end) {
        return;
    }

    await fetchJson("/api/connections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            start,
            connection: via,
            end,
        }),
    });

    state.addStart = "";
    state.addVia = "";
    state.addEnd = "";

    await loadConnections();
    render();
}

async function deleteConnection(id) {
    await fetchOk(`/api/connections?id=${encodeURIComponent(id)}`, {
        method: "DELETE",
    });

    await loadConnections();
    render();
}

// ---------------------------------------------------------------------------
// Connections UI
// ---------------------------------------------------------------------------

export function createGraphConnections() {
    const count = layout.mutedText(
        `${state.connections.length}${state.connHasMore ? "+" : ""} connections`
    );

    const rows = state.connections.map((item) => createConnectionRow(item));

    const children = [
        createAddConnectionRow(),
        createConnectionSearchRow(),
        count,
        ...rows,
    ];

    if (state.connHasMore) {
        const loadMoreBtn = createTextButton({ text: "Load more" });

        loadMoreBtn.addEventListener("click", async () => {
            await loadConnections(true);
            render();
        });

        children.push(loadMoreBtn);
    }

    return layout.stack(children);
}

function createAddConnectionRow() {
    const startInput = createLineEdit({
        value: state.addStart,
        placeholder: "Start",
    });

    startInput.addEventListener("input", () => {
        state.addStart = startInput.value;
    });

    startInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            addConnection();
        }
    });

    const viaInput = createLineEdit({
        value: state.addVia,
        placeholder: "Connection",
    });

    viaInput.addEventListener("input", () => {
        state.addVia = viaInput.value;
    });

    viaInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            addConnection();
        }
    });

    const endInput = createLineEdit({
        value: state.addEnd,
        placeholder: "End",
    });

    endInput.addEventListener("input", () => {
        state.addEnd = endInput.value;
    });

    endInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            addConnection();
        }
    });

    const addBtn = createTextButton({
        text: "Add",
        color: "#8bd5ff",
    });

    addBtn.addEventListener("click", addConnection);

    return el("div", {
        style: {
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr auto",
            gap: "var(--layout-gap)",
        },
    }, [startInput, viaInput, endInput, addBtn]);
}

function createConnectionSearchRow() {
    const searchInput = createLineEdit({
        value: state.connSearch,
        placeholder: "Filter",
    });

    searchInput.addEventListener("input", () => {
        state.connSearch = searchInput.value;
    });

    searchInput.addEventListener("keydown", async (event) => {
        if (event.key === "Enter") {
            await loadConnections();
            render();
        }

        if (event.key === "Escape") {
            state.connSearch = "";
            await loadConnections();
            render();
        }
    });

    const searchBtn = createTextButton({
        text: "Search",
    });

    searchBtn.addEventListener("click", async () => {
        await loadConnections();
        render();
    });

    return layout.searchRow([searchInput, searchBtn], { singleAction: true });
}

function createConnectionRow(item) {
    const label = `${item.start ?? ""}  ›  ${item.connection ?? ""}  ›  ${item.end ?? ""}`;

    const delBtn = createTextButton({
        text: "Del",
    });

    delBtn.addEventListener("click", () => {
        deleteConnection(item.id);
    });

    return layout.splitActionRow(label, delBtn);
}
