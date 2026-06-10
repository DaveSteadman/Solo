import { createPage, createPanel } from "/common/framework/js/basic-layout.js";
import { createChatExchange } from "/common/framework/js/chat-exchange.js";
import { createIconButton } from "/common/framework/js/icon-button.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createTextArea } from "/common/framework/js/text-area.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import { createSuggestList, hideSuggestList, renderSuggestList } from "/common/framework/js/suggest-list.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
const suggestList = createSuggestList();

const state = {
    completions: {
        commands: [],
        descriptions: {},
        input_history: [],
        models: [],
        sessions: [],
        task_names: [],
        test_files: []
    },
    draft: {
        backend: "ollama",
        host: "",
        model: "",
        contextSize: "8192",
        maxToolRounds: "8",
        prompt: "",
        useTools: true
    },
    error: "",
    logLive: true,
    logWrap: true,
    loading: false,
    notice: "",
    pageSpec: null,
    promptHistoryDraft: null,
    promptHistoryIndex: -1,
    promptFocused: false,
    resizingPanels: false,
    run: null,
    runningPrompt: "",
    suggestBase: "",
    suggestItems: [],
    suggestSuffix: "",
    suggestIndex: -1,
    snapshot: null
};

const fallbackCommands = [
    "/ctx", "/defaults", "/help", "/llmserver", "/llmserverconfig", "/mcp",
    "/rounds", "/sandbox", "/stopmodel", "/task", "/tasks", "/tools", "/version"
];

const controlFactories = {
    agentChatPanel: createAgentChatPanel,
    agentLogPanel: createAgentLogPanel,
    agentQueueList: createAgentQueueList,
    agentQueuePanel: createAgentQueuePanel,
    iconButton: createActionIconButton,
    iconTextButton: createActionIconTextButton,
    modelStatusPanel: createModelStatusPanel,
    textButton: createActionTextButton
};

boot().catch(renderFatal);

async function boot() {
    state.pageSpec = await fetchJson("/ui/page.json");
    await hydrateRemoteLayoutState();
    await refreshSnapshot();
    await loadCompletions();
    render();
    window.addEventListener("solo:panels-resize-start", () => {
        state.resizingPanels = true;
    });
    window.addEventListener("solo:panels-resize-end", (event) => {
        state.resizingPanels = false;
        mirrorLayoutState(event.detail?.storageKey).catch(() => {
            return;
        });
    });
    window.setInterval(() => {
        if (!canAutoRender()) {
            return;
        }
        refreshSnapshot().then(() => {
            if (canAutoRender()) {
                render();
            }
        }).catch((error) => {
            state.error = error.message;
            if (canAutoRender()) {
                render();
            }
        });
    }, 2500);
}

function canAutoRender() {
    return !state.resizingPanels && !state.promptFocused;
}

function render() {
    hideSuggest();
    mount.replaceChildren(createPage(state.pageSpec, createControl));
}

function renderFatal(error) {
    mount.replaceChildren(layout.shell([
        layout.normalText(error.message)
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

function createActionIconButton(spec) {
    const button = createIconButton(spec);
    wireAction(button, spec.action);
    return button;
}

function createActionTextButton(spec) {
    const normalized = normalizeActionButtonSpec(spec);
    const button = createTextButton(normalized);
    wireAction(button, spec.action);
    if (spec.disabled) {
        button.disabled = true;
    }
    return button;
}

function wireAction(button, action) {
    if (!action) {
        return;
    }
    button.addEventListener("click", () => {
        runAction(action).catch((error) => {
            state.error = error.message;
            state.loading = false;
            render();
        });
    });
}

async function runAction(action) {
    state.error = "";
    if (action === "refresh") {
        await refreshSnapshot();
        state.notice = "Refreshed.";
        render();
        return;
    }
    if (action === "runPrompt") {
        await runPrompt();
        return;
    }
    if (action === "toggleLive") {
        state.logLive = !state.logLive;
        render();
        return;
    }
    if (action === "toggleWrap") {
        state.logWrap = !state.logWrap;
        render();
        return;
    }
    if (action === "logTop") {
        scrollLog("top");
        return;
    }
    if (action === "logBottom") {
        scrollLog("bottom");
    }
}

async function refreshSnapshot() {
    state.snapshot = await fetchJson("/api/snapshot");
    const defaults = state.snapshot?.defaults ?? {};
    if (!state.draft.model && defaults.model) {
        state.draft.model = defaults.model;
    }
    if (!state.draft.host && defaults.host) {
        state.draft.host = defaults.host;
    }
    state.draft.backend = state.draft.backend || defaults.backend || "ollama";
    state.draft.contextSize = state.draft.contextSize || String(defaults.contextSize ?? 8192);
    state.draft.maxToolRounds = state.draft.maxToolRounds || String(defaults.maxToolRounds ?? 8);
}

async function loadCompletions() {
    try {
        state.completions = await fetchJson("/api/completions");
    } catch {
        state.completions = {
            commands: fallbackCommands,
            descriptions: {},
            input_history: [],
            models: [],
            sessions: [],
            task_names: [],
            test_files: []
        };
    }
}

async function runPrompt() {
    const prompt = state.draft.prompt.trim();
    if (!prompt) {
        state.notice = "Prompt is empty.";
        render();
        return;
    }
    state.loading = true;
    state.runningPrompt = prompt;
    state.draft.prompt = "";
    state.promptHistoryDraft = null;
    state.promptHistoryIndex = -1;
    state.notice = "Running.";
    render();
    try {
        const response = await postJson("/api/run", {
            prompt,
            backend: state.draft.backend,
            host: state.draft.host,
            model: state.draft.model,
            contextSize: Number.parseInt(state.draft.contextSize, 10),
            maxToolRounds: Number.parseInt(state.draft.maxToolRounds, 10),
            useTools: state.draft.useTools
        });
        state.run = response.run;
        state.notice = state.run?.error ? "Run failed." : "Run complete.";
        await refreshSnapshot();
    } finally {
        state.loading = false;
        state.runningPrompt = "";
        render();
    }
}

function createAgentLogPanel() {
    const log = layout.preformatted(state.snapshot?.runningLog ?? "No log loaded.", {
        nowrap: !state.logWrap
    });
    log.id = "agent-running-log";
    requestAnimationFrame(() => {
        if (state.logLive) {
            log.scrollTop = log.scrollHeight;
        }
    });
    return log;
}

function createAgentQueuePanel() {
    const count = queueEntriesForDisplay().length;
    return createPanel({
        title: `Queued Prompts: ${count}`,
        items: [
            { type: "agentQueueList" }
        ]
    }, createControl);
}

function createAgentQueueList() {
    return layout.preformatted(queueText(), {
        nowrap: true,
        fiveLines: true
    });
}

function createModelStatusPanel() {
    const defaults = state.snapshot?.defaults ?? {};
    const server = state.draft.host || defaults.host || defaultHost(state.draft.backend || defaults.backend);
    return layout.controlRow([
        statusPill("server", server || "-", "#78b0ff"),
        statusPill("model", state.draft.model || defaults.model || "-", "#63d9a4"),
        statusPill("context", state.draft.contextSize || String(defaults.contextSize ?? "-"), "#f6c177"),
        statusPill("data", state.snapshot?.dataRoot ?? "-", "#f6c177")
    ]);
}

function statusPill(label, value, color) {
    return createTextLabel({
        text: `${label}: ${value}`,
        color
    });
}

function createPromptBlock() {
    const prompt = createTextArea({
        value: state.draft.prompt,
        placeholder: "Prompt SoloAgent...",
        rows: 4
    });
    prompt.addEventListener("input", () => {
        state.draft.prompt = prompt.value;
        state.promptHistoryDraft = null;
        state.promptHistoryIndex = -1;
        requestAnimationFrame(() => updateSuggest(prompt));
    });
    prompt.addEventListener("focus", () => {
        state.promptFocused = true;
        loadCompletions().catch(() => {});
        updateSuggest(prompt);
    });
    prompt.addEventListener("blur", () => {
        state.promptFocused = false;
        window.setTimeout(hideSuggest, 120);
    });
    prompt.addEventListener("keydown", (event) => {
        if (handleSuggestKeydown(event, prompt)) {
            return;
        }
        if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
            return;
        }
        event.preventDefault();
        runPrompt().catch((error) => {
            state.error = error.message;
            state.loading = false;
            render();
        });
    });
    return layout.stack([
        layout.sectionTitle("Prompt"),
        prompt
    ]);
}

function handleSuggestKeydown(event, prompt) {
    if (event.key === "Tab") {
        event.preventDefault();
        if (state.suggestItems.length > 0) {
            if (state.suggestIndex >= 0) {
                selectSuggest(prompt, state.suggestIndex);
            } else {
                state.suggestIndex = 0;
                renderSuggest(prompt);
            }
            return true;
        }
        updateSuggest(prompt);
        if (state.suggestItems.length === 1) {
            selectSuggest(prompt, 0);
        }
        return true;
    }
    if (event.key === "Escape" && state.suggestItems.length > 0) {
        event.preventDefault();
        hideSuggest();
        return true;
    }
    if (event.key === "ArrowDown" && state.suggestItems.length > 0) {
        event.preventDefault();
        state.suggestIndex = Math.min(state.suggestIndex + 1, state.suggestItems.length - 1);
        renderSuggest(prompt);
        return true;
    }
    if (event.key === "ArrowUp" && state.suggestItems.length > 0) {
        event.preventDefault();
        state.suggestIndex = Math.max(state.suggestIndex - 1, 0);
        renderSuggest(prompt);
        return true;
    }
    if (event.key === "ArrowUp") {
        const history = state.completions.input_history ?? [];
        if (!history.length) {
            return false;
        }
        event.preventDefault();
        if (state.promptHistoryIndex === -1) {
            state.promptHistoryDraft = prompt.value;
            state.promptHistoryIndex = history.length - 1;
        } else {
            state.promptHistoryIndex = Math.max(0, state.promptHistoryIndex - 1);
        }
        setPromptValue(prompt, String(history[state.promptHistoryIndex] ?? ""));
        return true;
    }
    if (event.key === "ArrowDown") {
        const history = state.completions.input_history ?? [];
        if (!history.length || state.promptHistoryIndex === -1) {
            return false;
        }
        event.preventDefault();
        if (state.promptHistoryIndex < history.length - 1) {
            state.promptHistoryIndex += 1;
            setPromptValue(prompt, String(history[state.promptHistoryIndex] ?? ""));
        } else {
            state.promptHistoryIndex = -1;
            setPromptValue(prompt, state.promptHistoryDraft ?? "");
            state.promptHistoryDraft = null;
        }
        return true;
    }
    if (event.key === "Enter" && !event.shiftKey && state.suggestItems.length > 0 && state.suggestIndex >= 0) {
        event.preventDefault();
        selectSuggest(prompt, state.suggestIndex);
        return true;
    }
    return false;
}

function updateSuggest(prompt) {
    const context = parseSuggestContext(prompt);
    if (!context) {
        hideSuggest();
        return;
    }
    const prefix = context.prefix.toLowerCase();
    const items = context.pool
        .filter((item) => item.toLowerCase().startsWith(prefix))
        .map((item) => ({
            value: item,
            detail: context.details?.[item] ?? ""
        }));
    if (items.length === 0) {
        hideSuggest();
        return;
    }
    state.suggestBase = context.base;
    state.suggestSuffix = context.suffix;
    state.suggestItems = items;
    state.suggestIndex = -1;
    renderSuggest(prompt);
}

function renderSuggest(prompt) {
    renderSuggestList(suggestList, {
        anchor: prompt,
        items: state.suggestItems,
        activeIndex: state.suggestIndex,
        onSelect: (index) => selectSuggest(prompt, index)
    });
}

function hideSuggest() {
    hideSuggestList(suggestList);
    state.suggestItems = [];
    state.suggestIndex = -1;
    state.suggestBase = "";
    state.suggestSuffix = "";
}

function selectSuggest(prompt, index) {
    const item = state.suggestItems[index];
    if (!item) {
        return;
    }
    const insert = `${state.suggestBase}${item.value} `;
    setPromptValue(prompt, `${insert}${state.suggestSuffix}`);
    prompt.focus();
    prompt.setSelectionRange(insert.length, insert.length);
    hideSuggest();
    updateSuggest(prompt);
}

function setPromptValue(prompt, value) {
    prompt.value = value;
    state.draft.prompt = value;
}

function parseSuggestContext(prompt) {
    const start = prompt.selectionStart ?? prompt.value.length;
    const before = prompt.value.slice(0, start);
    const suffix = prompt.value.slice(start);
    if (!before.startsWith("/") || before.includes("\n")) {
        return null;
    }
    const firstSpace = before.indexOf(" ");
    if (firstSpace === -1) {
        return {
            pool: state.completions.commands?.length ? state.completions.commands : fallbackCommands,
            details: state.completions.descriptions ?? {},
            prefix: before,
            base: "",
            suffix
        };
    }

    const command = before.slice(0, firstSpace);
    const rest = before.slice(firstSpace + 1);
    if (command === "/llmserver") {
        return suggestToken(rest, "/llmserver ", ["ollama", "lmstudio", "openai"], suffix);
    }
    if (command === "/llmserverconfig") {
        const sub = firstToken(rest);
        if (!sub.hasSpace) {
            return suggestToken(rest, "/llmserverconfig ", ["model", "ctx", "rounds"], suffix);
        }
        if (sub.value === "model") {
            return suggestToken(sub.rest, `/llmserverconfig ${sub.value} `, ["list", ...(state.completions.models ?? [])], suffix);
        }
        return null;
    }
    if (command === "/ctx") {
        return suggestToken(rest, "/ctx ", ["size"], suffix);
    }
    if (command === "/sandbox") {
        return suggestToken(rest, "/sandbox ", ["on", "off"], suffix);
    }
    if (command === "/mcp") {
        return suggestToken(rest, "/mcp ", ["status", "reconnect"], suffix);
    }
    if (command === "/defaults") {
        return suggestToken(rest, "/defaults ", ["set"], suffix);
    }
    if (command === "/task") {
        const sub = firstToken(rest);
        const taskSubs = ["get", "add", "enable", "disable", "delete", "schedule", "prompt", "run"];
        if (!sub.hasSpace) {
            return suggestToken(rest, "/task ", taskSubs, suffix);
        }
        if (["get", "enable", "disable", "delete", "schedule", "prompt", "run"].includes(sub.value)) {
            return suggestToken(sub.rest, `/task ${sub.value} `, state.completions.task_names ?? [], suffix);
        }
    }
    return null;
}

function firstToken(text) {
    const space = text.indexOf(" ");
    if (space === -1) {
        return { value: text, rest: "", base: text, hasSpace: false };
    }
    return {
        value: text.slice(0, space),
        rest: text.slice(space + 1),
        base: text.slice(0, space),
        hasSpace: true
    };
}

function suggestToken(text, base, pool, suffix) {
    if (text.includes(" ")) {
        return null;
    }
    return {
        pool,
        prefix: text.trimEnd(),
        base,
        suffix
    };
}

function createAgentChatPanel() {
    return layout.stack([
        layout.stack([
            createChatExchange({ messages: chatMessages() })
        ], { fill: true }),
        layout.stack([
            createPromptBlock(),
            layout.actionRow([
                createActionTextButton({ text: state.loading ? "Running" : "Run", color: "#63d9a4", action: "runPrompt", disabled: state.loading }),
                createActionTextButton({ text: "Refresh", color: "#78b0ff", action: "refresh" })
            ])
        ]),
    ], { formFill: true });
}

function normalizeActionButtonSpec(spec) {
    if (spec.action === "toggleLive") {
        return {
            ...spec,
            text: "Live",
            color: state.logLive ? "#63d9a4" : "#a5afbf"
        };
    }
    if (spec.action === "toggleWrap") {
        return {
            ...spec,
            text: "Wrap",
            color: state.logWrap ? "#78b0ff" : "#a5afbf"
        };
    }
    return spec;
}

function scrollLog(position) {
    const log = document.querySelector("#agent-running-log");
    if (!log) {
        return;
    }
    log.scrollTop = position === "top" ? 0 : log.scrollHeight;
    if (position === "top") {
        state.logLive = false;
        render();
    }
}

function defaultHost(backend) {
    if (backend === "lmstudio") {
        return "http://localhost:1234";
    }
    if (backend === "ollama") {
        return "http://localhost:11434";
    }
    return "";
}

function chatMessages() {
    const messages = [];
    const chatRows = state.snapshot?.agentChat?.messages ?? [];
    for (const row of chatRows) {
        messages.push({
            role: row.direction === "inbound" ? "user" : "agent",
            label: `${row.sender_display || (row.direction === "inbound" ? "User" : "SoloAgent")} | ${row.status || "received"}`,
            text: row.content || ""
        });
    }
    if (state.loading && state.runningPrompt.trim()) {
        messages.push({
            role: "user",
            label: "User | running",
            text: state.runningPrompt.trim()
        });
        messages.push({
            role: "agent",
            label: "Agent",
            text: "Running..."
        });
    }
    if (!messages.length) {
        messages.push({
            role: "agent",
            text: state.snapshot?.agentChat?.error
                ? `SoloChat connection failed: ${state.snapshot.agentChat.error}`
                : "Run a prompt to start the AgentChat exchange."
        });
    }
    return messages;
}

function queueText() {
    const queue = state.snapshot?.queuedPrompts ?? {};
    if (queue.error) {
        return `SoloChat queue unavailable: ${queue.error}`;
    }
    const entries = queueEntriesForDisplay();
    if (!entries.length) {
        return "No queued prompts.";
    }
    return entries.map((entry) =>
        `${entry.conversation_name || `Conversation ${entry.conversation_id || "?"}`} : ${promptPreview(entry.prompt || "")}`
    ).join("\n");
}

function queueEntriesForDisplay() {
    const entries = [...(state.snapshot?.queuedPrompts?.entries ?? [])];
    const prompt = state.runningPrompt.trim();
    if (state.loading && prompt && !entries.some((entry) => entry.prompt === prompt && entry.conversation_name === "AgentChat")) {
        entries.unshift({
            conversation_name: "AgentChat",
            prompt
        });
    }
    return entries;
}

function promptPreview(text) {
    const normalized = String(text || "").replace(/\s+/g, " ").trim();
    if (normalized.length <= 32) {
        return normalized;
    }
    return `${normalized.slice(0, 32)}...`;
}

function hubUiStateBaseUrl() {
    const protocol = window.location.protocol || "http:";
    const hostname = window.location.hostname || "127.0.0.1";
    return `${protocol}//${hostname}:9700/api/ui-state`;
}

async function hydrateRemoteLayoutState() {
    const storageKeys = collectStorageKeys(state.pageSpec);
    if (!storageKeys.length) {
        return;
    }
    await Promise.all(storageKeys.map(loadRemoteLayoutState));
}

function collectStorageKeys(spec) {
    const keys = new Set();

    function visit(value) {
        if (!value || typeof value !== "object") {
            return;
        }
        if (typeof value.storageKey === "string" && value.storageKey.trim()) {
            keys.add(value.storageKey.trim());
        }
        if (Array.isArray(value.items)) {
            for (const item of value.items) {
                visit(item);
            }
        }
        if (Array.isArray(value.content)) {
            for (const item of value.content) {
                visit(item);
            }
        }
    }

    visit(spec);
    return [...keys];
}

async function loadRemoteLayoutState(storageKey) {
    try {
        const response = await fetch(`${hubUiStateBaseUrl()}/${encodeURIComponent(storageKey)}`);
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        if (payload?.value === undefined || payload?.value === null) {
            return;
        }
        window.localStorage.setItem(storageKey, JSON.stringify(payload.value));
    } catch {
        return;
    }
}

async function mirrorLayoutState(storageKey) {
    if (!storageKey) {
        return;
    }
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
        return;
    }
    let value = null;
    try {
        value = JSON.parse(raw);
    } catch {
        return;
    }
    await fetch(`${hubUiStateBaseUrl()}/${encodeURIComponent(storageKey)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value })
    });
}

async function fetchJson(url) {
    const response = await fetch(url);
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.error || payload.message || `${response.status} ${response.statusText}`);
    }
    return payload;
}

async function postJson(url, payload) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (!response.ok) {
        throw new Error(body.error || body.message || `${response.status} ${response.statusText}`);
    }
    return body;
}
