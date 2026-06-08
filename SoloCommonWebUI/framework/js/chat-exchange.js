import { el } from "./dom.js";

export function createChatExchange(spec) {
    const messages = readMessages(spec);
    const list = el("div", { className: "chat-exchange-list" }, messages.map(createMessage));
    const control = el("section", {
        className: "chat-exchange",
        attrs: {
            "aria-label": spec.label ?? "Chat exchange",
            "role": "log"
        }
    }, [list]);

    requestAnimationFrame(() => {
        control.scrollTop = control.scrollHeight;
    });

    return control;
}

function readMessages(spec) {
    if (Array.isArray(spec.messages)) {
        return spec.messages;
    }
    if (!spec.messagesJson) {
        return [];
    }
    try {
        const parsed = JSON.parse(spec.messagesJson);
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [
            {
                role: "agent",
                text: "Could not parse chat messages JSON."
            }
        ];
    }
}

function createMessage(message) {
    const role = message.role === "user" ? "user" : "agent";
    return el("article", {
        className: `chat-message chat-message--${role}`,
        attrs: {
            "data-role": role
        }
    }, [
        el("div", { className: "chat-message-label font-normal", text: message.label ?? defaultLabel(role) }),
        el("div", { className: "chat-message-bubble font-normal", text: message.text ?? "" })
    ]);
}

function defaultLabel(role) {
    return role === "user" ? "User" : "Agent";
}
