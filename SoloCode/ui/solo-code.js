// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders the SoloCode JSON-specified UI and binds it to the local workspace API.

import { createPage } from "/common/framework/js/basic-layout.js";
import { createChatExchange } from "/common/framework/js/chat-exchange.js";
import { createCodeEditor } from "/common/framework/js/code-editor.js";
import { createFileExplorer } from "/common/framework/js/file-explorer.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextArea } from "/common/framework/js/text-area.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
const state = {
    chat: null,
    context: null,
    dirty: false,
    error: "",
    file: null,
    openFolders: [],
    pageSpec: null,
    prompt: "",
    rootDraft: "",
    selection: null,
    snapshot: null,
    tree: []
};

const controlFactories = {
    codeChat: createCodeChat,
    codeExplorer: createCodeExplorer,
    codeRootLine: createCodeRootLine,
    codeWorkspacePanel: createCodeWorkspacePanel,
    codeView: createCodeView,
    iconTextButton: createActionIconTextButton,
    textButton: createActionTextButton,
    textLabel: createTextLabel
};

boot().catch(renderFatal);

async function boot() {
    state.pageSpec = await fetchJson("/ui/page.json");
    await refreshAll();
    render();
}

function render() {
    mount.replaceChildren(createPage(state.pageSpec, createControl));
}

function renderFatal(error) {
    mount.replaceChildren(layout.shell([
        layout.errorText(error.message)
    ]));
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
    wireAction(button, spec.action);
    return button;
}

function createActionTextButton(spec) {
    const button = createTextButton(spec);
    wireAction(button, spec.action);
    return button;
}

function wireAction(button, action) {
    if (!action) {
        return;
    }
    button.addEventListener("click", () => {
        runAction(action).catch((error) => {
            state.error = error.message;
            render();
        });
    });
}

async function runAction(action) {
    state.error = "";
    if (action === "refresh") {
        await refreshAll();
        render();
        return;
    }
    if (action === "loadRoot") {
        await setRoot(state.rootDraft);
        return;
    }
    if (action === "saveFile") {
        await saveFile();
        render();
        return;
    }
    if (action === "reloadFile") {
        await reloadFile();
        render();
        return;
    }
    if (action === "sendPrompt") {
        await sendPrompt();
        render();
        return;
    }
    if (action === "refreshChat") {
        await refreshChat();
        render();
    }
}

async function refreshAll() {
    state.snapshot = await fetchJson("/api/snapshot");
    state.rootDraft = state.snapshot?.workspace?.root ?? "";
    const treePayload = await fetchJson("/api/tree");
    state.tree = treePayload.items ?? [];
    await refreshChat();
}

async function refreshChat() {
    state.chat = await fetchJson("/api/chat").catch(() => ({ messages: [] }));
}

async function setRoot(root) {
    state.snapshot = await postJson("/api/root", { root });
    state.rootDraft = state.snapshot?.workspace?.root ?? root;
    state.file = null;
    state.context = null;
    state.selection = null;
    state.openFolders = [];
    const treePayload = await fetchJson("/api/tree");
    state.tree = treePayload.items ?? [];
    await refreshChat();
    render();
}

async function openFile(path) {
    state.file = await fetchJson(`/api/file?path=${encodeURIComponent(path)}`);
    state.context = await fetchJson(`/api/context?path=${encodeURIComponent(path)}`);
    state.selection = null;
    state.dirty = false;
    render();
}

async function saveFile() {
    if (!state.file) {
        state.error = "No file is selected.";
        return;
    }
    const saved = await putJson(`/api/file?path=${encodeURIComponent(state.file.path)}`, {
        content: state.file.content,
        expected_hash: state.file.content_hash
    });
    state.file = saved;
    state.dirty = false;
}

async function reloadFile() {
    if (!state.file) {
        return;
    }
    await openFile(state.file.path);
}

async function sendPrompt() {
    const prompt = state.prompt.trim();
    if (!prompt) {
        state.error = "Prompt cannot be empty.";
        return;
    }
    await postJson("/api/chat", {
        prompt,
        path: state.file?.path ?? "",
        selection: state.selection ?? {},
        context: state.context ?? {}
    });
    state.prompt = "";
    await refreshChat();
}

function createCodeRootLine() {
    const input = createLineEdit({
        value: state.rootDraft,
        placeholder: "Workspace root"
    });
    input.addEventListener("input", () => {
        state.rootDraft = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            setRoot(state.rootDraft).catch((error) => {
                state.error = error.message;
                render();
            });
        }
    });
    return layout.stack([
        layout.searchRow([
            input,
            createActionTextButton({ text: "Load", color: "#63d9a4", action: "loadRoot" })
        ], { singleAction: true }),
        state.error ? layout.errorText(state.error) : null
    ].filter(Boolean));
}

function createCodeWorkspacePanel() {
    return layout.stack([
        createCodeRootLine(),
        createCodeExplorer()
    ], { fill: true });
}

function createCodeExplorer() {
    const explorer = createFileExplorer({
        label: "Workspace files",
        items: state.tree,
        selectedPath: state.file?.path ?? "",
        openFolders: state.openFolders
    });
    explorer.addEventListener("solo:fileSelect", (event) => {
        const path = event.detail?.file?.path;
        if (path) {
            openFile(path).catch((error) => {
                state.error = error.message;
                render();
            });
        }
    });
    explorer.addEventListener("solo:folderToggle", (event) => {
        state.openFolders = event.detail?.openFolders ?? state.openFolders;
    });
    return explorer;
}

function createCodeView() {
    if (!state.file) {
        return layout.stack([
            layout.mutedText("Select a text file from the explorer."),
            state.snapshot ? layout.metadataStrip([
                ["root", state.snapshot.workspace?.root ?? "-"],
                ["chat", state.snapshot.chat?.externalId ?? "-"]
            ]) : null
        ].filter(Boolean), { fill: true });
    }
    const editor = createCodeEditor({
        label: state.file.path,
        code: state.file.content
    });
    editor.addEventListener("solo:codeInput", (event) => {
        state.file.content = event.detail?.value ?? "";
        state.dirty = true;
    });
    editor.addEventListener("solo:codeSelection", (event) => {
        state.selection = event.detail;
    });
    return layout.stack([
        layout.metadataStrip([
            ["file", state.file.path],
            ["encoding", state.file.encoding],
            ["size", formatBytes(state.file.size)],
            ["state", state.dirty ? "dirty" : "clean"]
        ]),
        editor
    ], { fill: true });
}

function createCodeChat() {
    const prompt = createTextArea({
        value: state.prompt,
        rows: 5,
        placeholder: "Ask SoloAgent about this workspace or selected code."
    });
    prompt.addEventListener("input", () => {
        state.prompt = prompt.value;
    });
    const messages = (state.chat?.messages ?? []).map((message) => ({
        role: message.direction === "inbound" ? "user" : "agent",
        label: message.sender_display || (message.direction === "inbound" ? "SoloCode" : "Agent"),
        text: message.content
    }));
    return layout.stack([
        createChatExchange({ messages }),
        layout.controlRow([
            prompt,
            createActionTextButton({ text: "Send prompt", color: "#63d9a4", action: "sendPrompt" })
        ])
    ], { formFill: true });
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw new Error(await response.text() || `${url} returned HTTP ${response.status}`);
    }
    return response.json();
}

async function postJson(url, payload) {
    return fetchJson(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {})
    });
}

async function putJson(url, payload) {
    return fetchJson(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {})
    });
}

function formatBytes(value) {
    const size = Number(value || 0);
    if (size < 1024) {
        return `${size} B`;
    }
    if (size < 1024 * 1024) {
        return `${(size / 1024).toFixed(1)} KB`;
    }
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
