import { createControlRow, createHeading, createPage, createParagraph } from "/common/framework/js/basic-layout.js";
import { createIconButton } from "/common/framework/js/icon-button.js";
import { createIconPanel, createIconPanelGroup } from "/common/framework/js/icon-panel.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
let pageSpec = null;
let snapshot = null;

const controlFactories = {
    controlRow: (spec) => createControlRow(spec, createControl),
    heading: createHeading,
    hubPaths: createHubPaths,
    hubServiceList: createHubServiceList,
    hubSummary: createHubSummary,
    iconButton: createActionIconButton,
    iconTextButton: createActionIconTextButton,
    lineEdit: createLineEdit,
    paragraph: createParagraph,
    textButton: createTextButton,
    textLabel: createTextLabel
};

async function boot() {
    pageSpec = await fetchJson("/ui/page.json");
    snapshot = await fetchJson("/api/snapshot");
    render();
    window.setInterval(refreshSnapshot, 2500);
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
    wireAction(button, spec.action);
    return button;
}

function createActionIconButton(spec) {
    const button = createIconButton(spec);
    button.disabled = Boolean(spec.disabled);
    wireAction(button, spec.action);
    return button;
}

function wireAction(button, action) {
    if (!action) {
        return;
    }
    button.dataset.action = action;
    button.addEventListener("click", () => runHubAction(action));
}

async function runHubAction(action) {
    if (action === "refresh") {
        await refreshSnapshot();
        return;
    }
    if (action === "startAuto") {
        await postJson("/api/services/start-auto");
        await refreshSnapshot();
        return;
    }
    if (action === "stopAll") {
        await postJson("/api/services/stop-all");
        await refreshSnapshot();
        return;
    }
    if (action.startsWith("service:")) {
        const [, slug, serviceAction] = action.split(":");
        await runServiceAction(slug, serviceAction);
    }
}

async function runServiceAction(slug, action) {
    await postJson(`/api/services/${encodeURIComponent(slug)}/${action}`);
    await refreshSnapshot();
}

async function postJson(url) {
    return fetchJson(url, { method: "POST" });
}

async function refreshSnapshot() {
    snapshot = await fetchJson("/api/snapshot");
    render();
}

function createHubSummary() {
    const metrics = snapshot?.hub?.metrics ?? {};
    return layout.stack([
        layout.metricGrid([
            ["Configured", metrics.configured ?? 0],
            ["Running", metrics.running ?? 0],
            ["Reachable", metrics.reachable ?? 0],
            ["Missing", metrics.missing ?? 0]
        ]),
        layout.mutedText(snapshot?.hub?.root ?? "")
    ]);
}

function createHubPaths() {
    const paths = snapshot?.hub?.paths ?? {};
    return layout.pathList([
        ["Solo root", paths.soloRoot ?? snapshot?.hub?.root ?? ""],
        ["Data root", paths.dataRoot ?? ""]
    ]);
}

function createHubServiceList() {
    const services = snapshot?.services ?? [];
    if (!services.length) {
        return layout.normalText("No child processes are configured.");
    }

    return createIconPanelGroup(services.map(createHubServicePanel), {
        minWidth: "220px",
        attrs: {
            "aria-label": "Child processes"
        }
    });
}

function createHubServicePanel(service) {
    return createIconPanel({
        color: service.running ? "#63d9a4" : "#78b0ff",
        icon: selectHubServiceIcon(service.slug),
        overline: service.stateLabel,
        title: service.label,
        description: service.description,
        meta: [
            { label: "Slug", value: service.slug },
            { label: "Working directory", value: service.cwd || "-" },
            { label: "Status", value: service.stateLabel },
            service.url ? { label: "URL", value: service.url } : null
        ].filter(Boolean),
        actions: [
            {
                type: "iconButton",
                icon: "next",
                color: "#63d9a4",
                label: `Start ${service.label}`,
                action: `service:${service.slug}:start`,
                disabled: !service.startable || service.running
            },
            {
                type: "iconButton",
                icon: "export",
                color: "#ff8fab",
                label: `Stop ${service.label}`,
                action: `service:${service.slug}:stop`,
                disabled: !service.running
            },
            {
                type: "iconButton",
                icon: "action",
                color: "#78b0ff",
                label: `Restart ${service.label}`,
                action: `service:${service.slug}:restart`,
                disabled: !service.startable
            }
        ]
    }, createControl);
}

function selectHubServiceIcon(slug) {
    const icons = {
        soloagent: "agent",
        solochat: "chat",
        solollm: "llm",
        solodata: "data",
        sololibrary: "library",
        solodocs: "docs",
        solocode: "code"
    };

    return icons[slug] ?? "agent";
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([
        layout.errorText(error.message)
    ]));
});
