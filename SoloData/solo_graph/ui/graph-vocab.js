// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Vocab API calls and vocab UI components for SoloGraph.

import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { el } from "/common/framework/js/dom.js";
import * as layout from "/common/framework/js/layout-components.js";
import { state, render, fetchJson, fetchOk } from "./solo-graph.js";

// ---------------------------------------------------------------------------
// Vocab API
// ---------------------------------------------------------------------------

export async function loadVocab(append = false) {
    const offset = append ? state.vocabOffset : 0;
    const data = await fetchJson(`/api/vocab?limit=${state.vocabLimit + 1}&offset=${offset}`);

    const items = data.vocab ?? [];
    const page = items.slice(0, state.vocabLimit);

    state.vocabHasMore = items.length > state.vocabLimit;
    state.vocab = append ? [...state.vocab, ...page] : page;
    state.vocabOffset = offset + page.length;
}

async function addVocab() {
    const term = state.addVocabTerm.trim();

    if (!term) {
        return;
    }

    await fetchJson("/api/vocab", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term }),
    });

    state.addVocabTerm = "";

    await loadVocab();
    render();
}

async function saveVocab() {
    const term = state.editingVocabTerm.trim();

    if (!term) {
        return;
    }

    await fetchJson(`/api/vocab?id=${encodeURIComponent(state.editingVocabId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term }),
    });

    state.editingVocabId = null;
    state.editingVocabTerm = "";

    await loadVocab();
    render();
}

async function deleteVocab(id) {
    await fetchOk(`/api/vocab?id=${encodeURIComponent(id)}`, {
        method: "DELETE",
    });

    if (state.editingVocabId === id) {
        state.editingVocabId = null;
        state.editingVocabTerm = "";
    }

    await loadVocab();
    render();
}

function cancelVocabEdit() {
    state.editingVocabId = null;
    state.editingVocabTerm = "";
    render();
}

// ---------------------------------------------------------------------------
// Vocab UI
// ---------------------------------------------------------------------------

export function createGraphVocab() {
    const termInput = createLineEdit({
        value: state.addVocabTerm,
        placeholder: "New term",
    });

    termInput.addEventListener("input", () => {
        state.addVocabTerm = termInput.value;
    });

    termInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            addVocab();
        }
    });

    const addBtn = createTextButton({
        text: "Add",
        color: "#8bd5ff",
    });

    addBtn.addEventListener("click", addVocab);

    const count = layout.mutedText(`${state.vocab.length}${state.vocabHasMore ? "+" : ""} terms`);

    const rows = state.vocab.map((item) => createVocabRow(item));

    const children = [
        layout.searchRow([termInput, addBtn], { singleAction: true }),
        count,
        ...rows,
    ];

    if (state.vocabHasMore) {
        const loadMoreBtn = createTextButton({ text: "Load more" });

        loadMoreBtn.addEventListener("click", async () => {
            await loadVocab(true);
            render();
        });

        children.push(loadMoreBtn);
    }

    return layout.stack(children);
}

function createVocabRow(item) {
    if (state.editingVocabId === item.id) {
        return createEditingVocabRow();
    }

    const editBtn = createTextButton({ text: "Edit" });

    editBtn.addEventListener("click", () => {
        state.editingVocabId = item.id;
        state.editingVocabTerm = item.term;
        render();
    });

    const delBtn = createTextButton({ text: "Del" });

    delBtn.addEventListener("click", () => {
        deleteVocab(item.id);
    });

    const buttons = el("div", {
        style: {
            display: "flex",
            gap: "4px",
        },
    }, [editBtn, delBtn]);

    return layout.splitActionRow(item.term, buttons);
}

function createEditingVocabRow() {
    const input = createLineEdit({
        value: state.editingVocabTerm,
    });

    input.addEventListener("input", () => {
        state.editingVocabTerm = input.value;
    });

    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            saveVocab();
        }

        if (event.key === "Escape") {
            cancelVocabEdit();
        }
    });

    const saveBtn = createTextButton({
        text: "Save",
        color: "#63d9a4",
    });

    saveBtn.addEventListener("click", saveVocab);

    const cancelBtn = createTextButton({
        text: "Cancel",
    });

    cancelBtn.addEventListener("click", cancelVocabEdit);

    const buttons = el("div", {
        style: {
            display: "flex",
            gap: "4px",
        },
    }, [saveBtn, cancelBtn]);

    return layout.searchRow([input, buttons], { singleAction: true });
}
