// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders the SoloData page from ui/page.json and live service API responses.

import { createHeading, createPage, createParagraph } from "/common/framework/js/basic-layout.js";
import { createCheckbox } from "/common/framework/js/checkbox.js";
import { createIconButton } from "/common/framework/js/icon-button.js";
import { createIconPanel, createIconPanelGroup } from "/common/framework/js/icon-panel.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTabRow } from "/common/framework/js/tabs.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import { el } from "/common/framework/js/dom.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
let pageSpec = null;
let snapshot = null;
let search = {
    query: "",
    domains: ["feeds", "library", "reference", "rag", "graph"],
    domainsSearched: [],
    results: [],
    resultsByDomain: {},
    activeView: "cards",
    guideOpen: false
};

const sourcePanelStyles = {
    feeds: { icon: "feeds", color: "#63d9a4" },
    library: { icon: "library", color: "#78b0ff" },
    reference: { icon: "reference", color: "#f6c177" },
    rag: { icon: "rag", color: "#c4a7ff" },
    graph: { icon: "graph", color: "#8bd5ff" }
};

const controlFactories = {
    dataPaths: createDataPaths,
    dataSearchGuide: createDataSearchGuide,
    dataSearchResults: createDataSearchResults,
    dataServiceGrid: createDataServiceGrid,
    dataServiceStat: createDataServiceStat,
    dataServiceStatus: createDataServiceStatus,
    dataSourceList: createDataSourceList,
    dataSummary: createDataSummary,
    dataUnifiedSearch: createDataUnifiedSearch,
    checkbox: createCheckbox,
    heading: createHeading,
    iconButton: createActionIconButton,
    iconTextButton: createActionIconTextButton,
    lineEdit: createLineEdit,
    paragraph: createParagraph,
    textButton: createActionTextButton,
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
    wireAction(button, spec.action);
    return button;
}

function createActionIconButton(spec) {
    const button = createIconButton(spec);
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
    button.dataset.action = action;
    button.addEventListener("click", () => runAction(action));
}

async function runAction(action) {
    if (action === "refresh") {
        await refreshSnapshot();
        return;
    }
    if (action === "search") {
        await runSearch();
        return;
    }
    if (action.startsWith("open:")) {
        window.location.href = action.slice("open:".length);
        return;
    }
    if (action.startsWith("domain:")) {
        await chooseDomainAndSearch(action.slice("domain:".length), false);
        return;
    }
    if (action.startsWith("searchDomain:")) {
        await chooseDomainAndSearch(action.slice("searchDomain:".length), true);
    }
}

async function refreshSnapshot() {
    snapshot = await fetchJson("/api/snapshot");
    render();
}

async function runSearch() {
    const payload = {
        query: search.query,
        domains: search.domains,
        limit: Number(search.limit || 20)
    };
    const response = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
    if (!response.ok) {
        throw new Error(`/api/search returned HTTP ${response.status}`);
    }
    const data = await response.json();
    search = {
        ...search,
        query: data.query,
        domainsSearched: data.domains_searched ?? [],
        results: data.results ?? [],
        resultsByDomain: data.results_by_domain ?? {},
        activeView: "cards"
    };
    render();
}

function createDataServiceGrid() {
    const sources = snapshot?.sources ?? [];
    return createIconPanelGroup(sources.map((source) => {
        const panelStyle = sourcePanelStyles[source.slug] ?? { icon: "tag", color: "#78b0ff" };
        const online = Boolean(source.running);
        return createIconPanel({
            icon: panelStyle.icon,
            overline: source.slug.toUpperCase(),
            title: source.label,
            description: source.description,
            color: online ? panelStyle.color : "#ff8fab",
            items: [
                { type: "dataServiceStatus", online },
                { type: "dataServiceStat", label: "files", value: String(source.fileCount ?? 0) },
                { type: "dataServiceStat", label: "updated", value: source.updated || "-" }
            ],
            actions: [
                { type: "textButton", text: "Browse", color: "#63d9a4", action: browseActionForSource(source) },
                { type: "textButton", text: "Search", color: "#78b0ff", action: `searchDomain:${source.slug}` }
            ]
        }, createControl);
    }), { minWidth: "220px" });
}

function browseActionForSource(source) {
    return source.uiUrl ? `open:${source.uiUrl}` : `domain:${source.slug}`;
}

function createDataServiceStatus(spec) {
    return layout.statusBadge(spec);
}

function createDataServiceStat(spec) {
    return layout.compactKeyValue(spec.label, spec.value);
}

async function chooseDomainAndSearch(domain, shouldSearch) {
    search.domains = [domain];
    if (shouldSearch && search.query) {
        await runSearch();
        return;
    }
    render();
}

function createDataSummary() {
    const metrics = snapshot?.service?.metrics ?? {};
    return layout.stack([
        layout.metricGrid([
            ["Sources", metrics.sources ?? 0],
            ["Folders", metrics.existingFolders ?? 0],
            ["Files", metrics.files ?? 0]
        ]),
        layout.mutedText(`Status: ${snapshot?.service?.status ?? "unknown"}`)
    ]);
}

function createDataPaths() {
    const paths = snapshot?.paths ?? {};
    return layout.pathList([
        ["Solo root", paths.soloRoot ?? ""],
        ["Data root", paths.dataRoot ?? ""],
        ["Service data", paths.serviceDataRoot ?? ""],
        ["Logs", paths.logDir ?? ""]
    ]);
}

function createDataUnifiedSearch() {
    const sourceDomains = (snapshot?.sources ?? []).map((source) => source.slug);
    if (!search.domains?.length) {
        search.domains = [...sourceDomains];
    }
    const input = createLineEdit({
        value: search.query,
        placeholder: "e.g. climate change arctic ice"
    });
    input.addEventListener("input", () => {
        search.query = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            runSearch();
        }
    });
    const button = createTextButton({ text: "Search", color: "#63d9a4" });
    button.addEventListener("click", runSearch);
    const searchRow = layout.searchRow([input, button], { singleAction: true });
    const filters = layout.filterRow([
        layout.mutedText("Services", { tag: "span" }),
        ...sourceDomains.map((domain) => domainToggle(domain))
    ]);
    const limitInput = createLineEdit({
        value: String(search.limit || 20),
        placeholder: "20"
    });
    limitInput.addEventListener("input", () => {
        search.limit = limitInput.value;
    });
    const limitRow = layout.filterRow([
        el("label", { text: "Limit" }),
        limitInput,
        layout.mutedText("per service", { tag: "span" })
    ]);
    return layout.stack([searchRow, filters, limitRow]);
}

function domainToggle(domain) {
    const checkbox = createCheckbox({
        text: labelForDomain(domain),
        value: domain,
        checked: search.domains.includes(domain)
    });
    const input = checkbox.querySelector("input");
    input.addEventListener("change", () => {
        const selected = new Set(search.domains);
        if (input.checked) {
            selected.add(domain);
        } else {
            selected.delete(domain);
        }
        search.domains = [...selected];
    });
    return checkbox;
}

function labelForDomain(domain) {
    const source = (snapshot?.sources ?? []).find((item) => item.slug === domain);
    return source?.label ?? domain;
}

function createDataSearchGuide() {
    const button = createTextButton({ text: search.guideOpen ? "Hide" : "Show", color: "#78b0ff" });
    button.addEventListener("click", () => {
        search.guideOpen = !search.guideOpen;
        render();
    });
    const rules = [
        ["art of war", "All words anywhere in the document", "Bare terms default to AND"],
        ["\"art of war\"", "Exact phrase", "Words must appear consecutively"],
        ["plato OR aristotle", "Either side may match", "Useful for alternate names"],
        ["stoic NOT roman", "Exclude a noisy term", "Useful when one word dominates results"],
        ["(plato OR socrates) dialogue", "Grouped alternatives", "Combines alternatives with another required term"]
    ];
    const body = search.guideOpen
        ? layout.stack([
            layout.table(["You type", "Matches", "Example"], rules),
            layout.mutedText("This searches running SoloData services through their published search APIs.")
        ])
        : null;
    return layout.stack([
        layout.splitActionRow(
            "Bare terms use AND by default. Use quotes, OR, NOT and parentheses as the target query language.",
            button
        ),
        body
    ].filter(Boolean));
}

function createDataSearchResults() {
    const results = search.results ?? [];
    if (!search.query) {
        return layout.mutedText("Results appear here after a search.");
    }
    const tabRow = createTabRow({
        activeValue: search.activeView,
        items: [
            { value: "cards", label: "Cards", color: "#63d9a4" },
            { value: "json", label: "JSON", color: "#78b0ff" }
        ],
        onSelect: (value) => {
            search.activeView = value;
            render();
        }
    });
    const content = search.activeView === "json" ? createJsonResults() : createCardResults(results);
    const meta = `"${search.query}" | ${results.length} result${results.length === 1 ? "" : "s"} across [${(search.domainsSearched ?? []).join(", ")}]`;
    return layout.stack([
        layout.mutedText(meta),
        tabRow,
        content
    ]);
}

function createCardResults(results) {
    const domains = [
        ...new Set([
            ...(search.domainsSearched ?? []),
            ...Object.keys(search.resultsByDomain ?? {})
        ])
    ];
    if (!results.length && !domains.length) {
        return layout.mutedText("No matching service results found.");
    }
    return layout.groupedResults(domains.map((domain) => {
        const domainResults = search.resultsByDomain?.[domain] ?? [];
        const hasError = domainResults && !Array.isArray(domainResults) && domainResults.error;
        return {
            label: domain.toUpperCase(),
            error: hasError ? domainResults.error : "",
            items: Array.isArray(domainResults) ? domainResults.map(normalizeResultCard) : [],
            emptyText: "No results."
        };
    }));
}

function normalizeResultCard(result) {
    return {
        type: result.type ?? "",
        title: result.title ?? "",
        source: result.source ?? "",
        path: result.path ?? "",
        snippet: result.snippet ?? ""
    };
}

function createJsonResults() {
    return layout.preformatted(
        JSON.stringify({
            query: search.query,
            domains_searched: search.domainsSearched,
            results_by_domain: search.resultsByDomain,
            results: search.results
        }, null, 2)
    );
}

function createDataSourceList() {
    const sources = snapshot?.sources ?? [];
    return layout.itemList(sources.map((source) => {
        return layout.item([
            layout.titleRow(source.label, `${source.fileCount} files`),
            layout.normalText(source.description),
            layout.codeText(source.path),
            layout.mutedText(source.updated ? `Updated ${source.updated}` : "No files yet")
        ]);
    }));
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([
        layout.errorText(error.message)
    ]));
});
