import { createPage } from "/common/framework/js/basic-layout.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import { formatDateTime, formatNumber } from "/common/framework/js/text-utils.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
const state = {
    detail: null,
    draft: "",
    draftFocused: false,
    error: "",
    notice: "",
    pageSpec: null,
    refreshTimer: null,
    selectedConversationId: null,
    snapshot: null
};

// MARK: Core controls

const controlFactories = {
    chatDetailBackgroundContext: createChatDetailBackgroundContext,
    chatDetailCompose: createChatDetailCompose,
    chatDetailDatasets: createChatDetailDatasets,
    chatDetailSelectionPrompt: createChatDetailSelectionPrompt,
    chatDetailEvents: createChatDetailEvents,
    chatDetailInputHistory: createChatDetailInputHistory,
    chatDetailMessages: createChatDetailMessages,
    chatDetailMetadata: createChatDetailMetadata,
    chatDetailScratchpad: createChatDetailScratchpad,
    chatDetailThreadSummary: createChatDetailThreadSummary,
    chatSelectionList: createChatSelectionList,
    iconTextButton: createActionIconTextButton
};

boot().catch(renderFatal);

async function boot() {
    state.pageSpec = await fetchJson("/ui/page.json");
    await refreshSnapshot({ preserveSelection: false });
    render();
    state.refreshTimer = window.setInterval(() => {
        const shouldRender = !state.draftFocused;
        refreshSnapshot({ preserveSelection: true, silent: true }).then(() => {
            if (shouldRender) {
                render();
            }
        }).catch((error) => {
            state.error = error.message;
            if (shouldRender) {
                render();
            }
        });
    }, 3000);
}

function render() {
    mount.replaceChildren(createPage(buildPageSpec(), createControl));
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

function buildPageSpec() {
    const spec = structuredClone(state.pageSpec);
    const detailPanel = spec?.content?.[0]?.items?.find((item) => item?.title === "Chat Details");
    if (detailPanel) {
        detailPanel.actions = [
            {
                type: "iconTextButton",
                icon: "action",
                text: "Delete",
                color: "#ff8fab",
                action: "deleteConversation",
                disabled: !state.selectedConversationId
            }
        ];
    }
    return spec;
}

function createActionTextButton(spec) {
    const button = createTextButton(spec);
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
    button.dataset.action = action;
    button.addEventListener("click", () => {
        runAction(action).catch((error) => {
            state.error = error.message;
            render();
        });
    });
}

// MARK: Chat detail selection prompt

async function runAction(action) {
    state.error = "";
    if (action.startsWith("select:")) {
        const conversationId = Number.parseInt(action.slice("select:".length), 10);
        if (!Number.isNaN(conversationId)) {
            state.selectedConversationId = conversationId;
            state.notice = "";
            await loadSelectedDetail(false);
            render();
        }
        return;
    }
    if (action === "refresh") {
        state.notice = "Refreshed.";
        await refreshSnapshot({ preserveSelection: true });
        render();
        return;
    }
    if (action === "newConversation") {
        const created = await postJson("/api/conversations", {
            channel_type: "service",
            profile: "admin",
            subject: "New conversation"

        });
        state.selectedConversationId = created.id ?? null;
        state.notice = "Created conversation.";
        await refreshSnapshot({ preserveSelection: true });
        render();
        return;
    }
    if (action === "sendInbound") {
        await sendDraft("inbound");
        return;

    }
    if (action === "sendOutbound") {
        await sendDraft("outbound");
        return;
    }
    if (action === "clearDraft") {
        state.draft = "";
        state.notice = "Cleared draft.";
        render();

        return;
    }
    if (action === "deleteConversation") {
        if (!state.selectedConversationId) {
            return;
        }
        await deleteJson(`/api/conversations/${state.selectedConversationId}`);
        state.selectedConversationId = null;
        state.detail = null;
        state.notice = "Deleted conversation.";

        await refreshSnapshot({ preserveSelection: false });
        render();
    }
}

async function sendDraft(direction) {
    const content = state.draft.trim();
    if (!content) {
        state.notice = "Draft is empty.";
        render();

        return;
    }
    if (!state.selectedConversationId) {
        const created = await postJson("/api/conversations", {
            channel_type: "service",
            profile: "admin",
            subject: "New conversation"
        });
        state.selectedConversationId = created.id ?? null;
    }

    await postJson(`/api/conversations/${state.selectedConversationId}/messages`, {
        direction,
        content,
        sender_display: direction === "inbound" ? "User" : "Assistant",
        status: direction === "inbound" ? "received" : "sent"
    });
    state.draft = "";
    state.notice = direction === "inbound" ? "Queued prompt." : "Added outbound reply.";
    await refreshSnapshot({ preserveSelection: true });
    render();

}

async function refreshSnapshot(options = {}) {
    const preserveSelection = options.preserveSelection !== false;
    const silent = options.silent === true;
    state.snapshot = await fetchJson("/api/snapshot");
    const conversations = state.snapshot?.recentConversations ?? [];
    const hasSelected = preserveSelection && conversations.some((conversation) => conversation.id === state.selectedConversationId);
    if (!hasSelected) {
        state.selectedConversationId = conversations[0]?.id ?? null;

    }
    await loadSelectedDetail(silent);
}

async function loadSelectedDetail(silent) {
    if (!state.selectedConversationId) {
        state.detail = null;
        return;
    }

    try {
        state.detail = await fetchJson(`/api/conversations/${state.selectedConversationId}/detail`);
        if (!silent) {
            state.error = "";
        }
    } catch (error) {
        state.detail = null;
        if (!silent) {
            state.error = error.message;
        }
    }
}

// MARK: Chat list

function createChatSelectionList() {
    const conversations = sortedConversations();
    const eventCounts = state.snapshot?.counts?.events ?? {};
    return layout.stack([
        layout.controlRow([
            createTextLabel({ text: `${conversations.length} recent`, color: "#78b0ff" }),
            createTextLabel({ text: `${eventCounts.pending ?? 0} pending`, color: "#ff8fab" }),
            createTextLabel({ text: `${eventCounts.claimed ?? 0} claimed`, color: "#63d9a4" })
        ]),
        layout.controlRow([
            createActionTextButton({ text: "New", color: "#63d9a4", action: "newConversation" }),
            createActionTextButton({ text: "Refresh", color: "#78b0ff", action: "refresh" })
        ]),
        state.error ? layout.normalText(state.error) : null,
        state.notice ? layout.normalText(state.notice) : null,
        conversations.length
            ? layout.itemList(conversations.map(createConversationCard))
            : layout.normalText("No conversations.")
    ].filter(Boolean));
}

function createConversationCard(conversation) {
    const selected = conversation.id === state.selectedConversationId;
    return layout.item([
        layout.titleRow(conversationSubject(conversation), `#${conversation.id}`),
        layout.mutedText(`${conversation.channel_type || "service"} | ${formatDateTime(conversation.last_activity_at)}`),
        layout.controlRow([
            createTextLabel({ text: displayStatus(conversation.status), color: statusColor(conversation.status) }),
            createTextLabel({ text: conversation.profile || "external", color: "#78b0ff" }),
            selected
                ? createTextLabel({ text: "selected", color: "#f6c177" })
                : createActionTextButton({ text: "Open", color: "#63d9a4", action: `select:${conversation.id}` })
        ])
    ]);
}

function selectedConversation() {
    return state.detail?.conversation ?? null;
}

// MARK: Chat detail selection prompt

function createChatDetailSelectionPrompt() {
    if (selectedConversation()) {
        return null;
    }

    return layout.stack([
        layout.mutedText("Choose a chat from the list."),
        layout.controlRow([
            createActionTextButton({ text: "Create conversation", color: "#63d9a4", action: "newConversation" })
        ])
    ]);
}

// MARK: Chat detail metadata

function createChatDetailMetadata() {
    const conversation = selectedConversation();
    if (!conversation) {
        return null;
    }

    return createChatDetailMetadataBlock(conversation);
}

function createChatDetailMetadataBlock(conversation) {
    const identityEntries = [
        ["id", String(conversation.id ?? "-")],
        ["status", displayStatus(conversation.status)],
        ["profile", conversation.profile || "external"],
        ["channel", conversation.channel_type || "service"],
        ["external", conversation.external_id || "-"]
    ];
    const summaryEntries = [
        ["protected", Number(conversation.protected || 0) === 1 ? "yes" : "no"],
        ["turns",    formatNumber(conversation.turn_count)],
        ["tokens",   formatNumber(conversation.token_estimate)],
        ["messages", formatNumber((state.detail?.messages ?? []).length)],
        ["events",   formatNumber((state.detail?.events ?? []).length)]
    ];
    const timestampEntries = [
        ["last",    formatDateTime(conversation.last_activity_at)],
        ["created", formatDateTime(conversation.created_at)],
        ["updated", formatDateTime(conversation.updated_at)]
    ];

    return layout.item([
        layout.titleRow("Metadata", conversationName(conversation)),
        layout.stack([
            layout.metadataStrip(identityEntries),
            layout.metadataStrip(summaryEntries),
            layout.metadataStrip(timestampEntries)
        ])
    ]);
}

// MARK: Chat detail compose

function createChatDetailCompose() {
    if (!selectedConversation()) {
        return null;
    }

    return createChatDetailComposeBlock();
}

function createChatDetailComposeBlock() {
    const lineEdit = createLineEdit({
        value: state.draft,
        placeholder: "Type a prompt or reply and press Enter."
    });
    lineEdit.addEventListener("input", () => {
        state.draft = lineEdit.value;
    });
    lineEdit.addEventListener("focus", () => {
        state.draftFocused = true;
    });
    lineEdit.addEventListener("blur", () => {
        state.draftFocused = false;
    });
    lineEdit.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            runAction("sendInbound").catch((error) => {
                state.error = error.message;
                render();
            });
        }
    });
    return layout.item([
        layout.titleRow("Send Message"),
        layout.controlRow([
            lineEdit,
            createActionTextButton({ text: "Send prompt", color: "#63d9a4", action: "sendInbound" }),
            createActionTextButton({ text: "Send reply", color: "#78b0ff", action: "sendOutbound" }),
            createActionTextButton({ text: "Clear", color: "#ff8fab", action: "clearDraft" })
        ])
    ]);
}

// MARK: Chat detail background context

function createChatDetailBackgroundContext() {
    const conversation = selectedConversation();
    if (!conversation) {
        return null;
    }

    return createChatDetailTextBlock("Background Context", conversation.background_context, "No background context.");
}

// MARK: Chat detail thread summary

function createChatDetailThreadSummary() {
    const conversation = selectedConversation();
    if (!conversation) {
        return null;
    }

    return createChatDetailTextBlock("Thread Summary", conversation.thread_summary, "No thread summary.");
}

function createChatDetailTextBlock(title, text, emptyText) {
    return layout.item([
        layout.titleRow(title),
        text && String(text).trim()
            ? layout.preformatted(String(text))
            : layout.normalText(emptyText)
    ]);
}

// MARK: Chat detail scratchpad

function createChatDetailScratchpad() {
    const conversation = selectedConversation();
    if (!conversation) {
        return null;
    }

    return createChatDetailObjectBlock("Scratchpad", conversation.scratchpad, "Scratchpad is empty.");
}

// MARK: Chat detail datasets

function createChatDetailDatasets() {
    const conversation = selectedConversation();
    if (!conversation) {
        return null;
    }

    return createChatDetailObjectBlock("Datasets", conversation.datasets, "Datasets are empty.");
}

function createChatDetailObjectBlock(title, value, emptyText) {
    const entries = Object.entries(normalizeObject(value));
    if (!entries.length) {
        return layout.item([
            layout.titleRow(title),
            layout.normalText(emptyText)
        ]);
    }
    return layout.item([
        layout.titleRow(`${title} (${entries.length})`),
        layout.itemList(entries.map(([key, entryValue]) => layout.item([
            layout.titleRow(key, valueType(entryValue)),
            layout.preformatted(formatStructuredValue(entryValue) || "(empty)")
        ])))
    ]);
}

// MARK: Chat detail input history

function createChatDetailInputHistory() {
    const conversation = selectedConversation();
    if (!conversation) {
        return null;
    }

    return createChatDetailInputHistoryBlock(conversation.input_history || []);
}

function createChatDetailInputHistoryBlock(entries) {
    const items = Array.isArray(entries) ? entries.slice().reverse() : [];
    if (!items.length) {
        return layout.item([
            layout.titleRow("Input History"),
            layout.normalText("Input history is empty.")
        ]);
    }
    return layout.item([
        layout.titleRow(`Input History (${items.length})`),
        layout.itemList(items.map((entry, index) => layout.item([
            layout.titleRow(`Prompt ${items.length - index}`),
            layout.preformatted(String(entry || ""))
        ])))
    ]);
}

// MARK: Chat detail messages

function createChatDetailMessages() {
    if (!selectedConversation()) {
        return null;
    }

    return createChatDetailMessagesBlock(state.detail?.messages ?? []);
}

function createChatDetailMessagesBlock(messages) {
    if (!messages.length) {
        return layout.item([
            layout.titleRow("Messages"),
            layout.normalText("No messages.")
        ]);
    }
    return layout.item([
        layout.titleRow(`Messages (${messages.length})`),
        layout.itemList(messages.map(createChatDetailMessageCard))
    ]);
}

function createChatDetailMessageCard(message) {
    return layout.item([
        layout.titleRow(`#${message.id} ${message.direction || "message"}`, message.status || "received"),
        layout.mutedText(`${message.sender_display || "-"} | ${formatDateTime(message.created_at)}`),
        layout.preformatted(String(message.content || ""))
    ]);
}

// MARK: Chat detail events

function createChatDetailEvents() {
    if (!selectedConversation()) {
        return null;
    }

    return createChatDetailEventsBlock(state.detail?.events ?? []);
}

function createChatDetailEventsBlock(events) {
    if (!events.length) {
        return layout.item([
            layout.titleRow("Events"),
            layout.normalText("No events.")
        ]);
    }
    return layout.item([
        layout.titleRow(`Events (${events.length})`),
        layout.itemList(events.map(createChatDetailEventCard))
    ]);
}

function createChatDetailEventCard(eventRecord) {
    const payloadText = formatStructuredValue(normalizePayload(eventRecord.payload));
    return layout.item([
        layout.titleRow(`#${eventRecord.id} ${eventRecord.event_type || "event"}`, eventRecord.status || "pending"),
        layout.mutedText(`priority ${formatNumber(eventRecord.priority)} | ${formatDateTime(eventRecord.created_at)}`),
        layout.mutedText(`claimed by ${eventRecord.claimed_by || "-"}`),
        payloadText ? layout.preformatted(payloadText) : null
    ].filter(Boolean));
}

function sortedConversations() {
    return [...(state.snapshot?.recentConversations ?? [])].sort((left, right) =>
        String(right.last_activity_at || "").localeCompare(String(left.last_activity_at || ""))
    );
}

function conversationSubject(conversation) {
    return String(conversation.subject || conversation.external_id || `Conversation ${conversation.id}`).trim();
}

function conversationName(conversation) {
    return `${conversationSubject(conversation)} [${conversation.id}]`;
}

function displayStatus(status) {
    return String(status || "active").replaceAll("_", " ");
}

function statusColor(status) {
    if (status === "waiting_agent") {
        return "#ff8fab";
    }
    if (status === "agent_processing") {
        return "#f6c177";
    }
    if (status === "archived") {
        return "#b7bdf8";
    }
    return "#63d9a4";
}

function normalizeObject(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        return value;
    }
    if (typeof value === "string") {
        try {
            const parsed = JSON.parse(value);
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                return parsed;
            }
        } catch {
            return {};
        }
    }
    return {};
}

function normalizePayload(value) {
    if (value && typeof value === "object") {
        return value;
    }
    if (typeof value === "string") {
        try {
            return JSON.parse(value);
        } catch {
            return value;
        }
    }
    return value ?? "";
}

function formatStructuredValue(value) {
    if (value === null || value === undefined) {
        return "";
    }
    if (typeof value === "string") {
        return value;
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

function valueType(value) {
    if (Array.isArray(value)) {
        return "array";
    }
    if (value === null || value === undefined) {
        return "empty";
    }
    return typeof value === "object" ? "object" : typeof value;
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw new Error(`${url} returned HTTP ${response.status}`);
    }
    return response.json();
}

async function postJson(url, payload) {
    return fetchJson(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(payload ?? {})
    });
}

async function deleteJson(url) {
    const response = await fetch(url, { method: "DELETE" });
    if (!response.ok) {
        throw new Error(`${url} returned HTTP ${response.status}`);
    }
    return response.json();
}
