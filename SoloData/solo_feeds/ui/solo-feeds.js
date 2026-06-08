// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders the SoloFeeds dashboard and domain detail views from service API responses.

import { createPage } from "/common/framework/js/basic-layout.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { el } from "/common/framework/js/dom.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
const state = {
    createDomainDraft: "",
    detail: {
        addFeed: {
            notes: "",
            title: "",
            updateMinutes: "60",
            url: ""
        },
        feedRateDrafts: {},
        search: {
            query: "",
            ran: false,
            results: []
        },
        settings: {
            days: "30",
            endDate: "",
            mode: "days_previous",
            startDate: ""
        },
        snapshot: null
    },
    flash: { error: "", notice: "" },
    pageSpec: null,
    route: parseRoute(),
    search: {
        domain: "",
        limit: "50",
        noOlderThanDays: "",
        query: "",
        results: [],
        ran: false,
        since: "",
        until: ""
    },
    snapshot: null
};

const controlFactories = {
    feedCreateDomain: createFeedCreateDomain,
    feedDetailAddFeed: createFeedDetailAddFeed,
    feedDetailEntries: createFeedDetailEntries,
    feedDetailFeeds: createFeedDetailFeeds,
    feedDetailManageEntries: createFeedDetailManageEntries,
    feedDetailSettings: createFeedDetailSettings,
    feedDomains: createFeedDomains,
    feedEntries: createFeedEntries,
    feedPaths: createFeedPaths,
    feedSearch: createFeedSearch,
    feedStatus: createFeedStatus,
    feedTable: createFeedTable,
    iconTextButton: createActionIconTextButton
};

async function boot() {
    state.pageSpec = await fetchJson("/ui/page.json");
    if (state.route.kind === "detail") {
        await refreshDomainSnapshot();
    } else {
        await refreshSnapshot();
    }
    render();
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw new Error(await readErrorMessage(response, `${url} returned HTTP ${response.status}`));
    }
    return response.json();
}

function render() {
    mount.replaceChildren(createPage(buildPageSpec(), createControl));
}

function buildPageSpec() {
    if (state.route.kind !== "detail") {
        return state.pageSpec;
    }
    return {
        ...state.pageSpec,
        header: {
            title: `SoloFeeds / ${state.route.domain}`,
            subtitle: "Domain feeds, schedules and saved entries",
            actions: [
                { type: "iconTextButton", icon: "preview", text: "Feeds", color: "#78b0ff", action: "goHome" },
                { type: "iconTextButton", icon: "preview", text: "Refresh", color: "#63d9a4", action: "refresh" }
            ]
        },
        content: [
            {
                type: "panels",
                columns: 2,
                items: [
                    {
                        title: "Feeds",
                        items: [{ type: "feedDetailFeeds" }]
                    },
                    {
                        title: "Entries",
                        items: [{ type: "feedDetailEntries" }]
                    }
                ]
            },
            {
                type: "panels",
                columns: 2,
                items: [
                    {
                        title: "Domain Age Settings",
                        items: [{ type: "feedDetailSettings" }]
                    },
                    {
                        title: "Add Feed",
                        items: [{ type: "feedDetailAddFeed" }]
                    }
                ]
            },
            {
                type: "panel",
                title: "Manage Entries",
                items: [{ type: "feedDetailManageEntries" }]
            }
        ]
    };
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
        button.addEventListener("click", () => {
            refresh().catch(handleActionError);
        });
    } else if (spec.action === "goHome") {
        button.addEventListener("click", () => {
            window.location.href = "/ui";
        });
    }
    return button;
}

async function refresh() {
    clearFlash();
    if (state.route.kind === "detail") {
        await refreshDomainSnapshot({ rerunSearch: state.detail.search.ran && Boolean(state.detail.search.query.trim()) });
    } else {
        await refreshSnapshot({ rerunSearch: state.search.ran && Boolean(state.search.query.trim()) });
    }
    render();
}

function createFeedStatus() {
    const metrics = state.snapshot?.metrics ?? {};
    return layout.metricGrid([
        ["Domains", metrics.domains ?? 0],
        ["Feeds", metrics.feeds ?? 0],
        ["Entries", metrics.entries ?? 0],
        ["Uptime", `${state.snapshot?.uptimeSec ?? 0}s`]
    ]);
}

function createFeedPaths() {
    const paths = state.snapshot?.paths ?? {};
    return layout.pathList([
        ["Feeds", paths.feedsRoot ?? ""],
        ["Database", paths.db ?? ""],
        ["Logs", paths.logs ?? ""]
    ]);
}

function createFeedSearch() {
    const input = createLineEdit({ value: state.search.query, placeholder: "search all feeds..." });
    input.addEventListener("input", () => {
        state.search.query = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            runSearch().catch(handleActionError);
        }
    });
    const button = createTextButton({ text: "Search", color: "#63d9a4" });
    button.addEventListener("click", () => {
        runSearch().catch(handleActionError);
    });
    const sinceInput = createTypedInput({ type: "date", value: state.search.since, placeholder: "Since" });
    sinceInput.addEventListener("input", () => {
        state.search.since = sinceInput.value;
    });
    const untilInput = createTypedInput({ type: "date", value: state.search.until, placeholder: "Until" });
    untilInput.addEventListener("input", () => {
        state.search.until = untilInput.value;
    });
    const ageInput = createTypedInput({ type: "number", value: state.search.noOlderThanDays, placeholder: "7", min: "0" });
    ageInput.addEventListener("input", () => {
        state.search.noOlderThanDays = ageInput.value;
    });
    const limitInput = createTypedInput({ type: "number", value: state.search.limit, placeholder: "50", min: "1" });
    limitInput.addEventListener("input", () => {
        state.search.limit = limitInput.value;
    });
    const resetButton = createTextButton({ text: "Reset", color: "#f6c177" });
    resetButton.addEventListener("click", resetSearch);
    return layout.stack([
        layout.normalText("Bare terms use AND by default. Use quotes for phrases, OR or | for alternatives, NOT to exclude, and parentheses to group."),
        layout.searchRow([input, button], { singleAction: true }),
        layout.formGrid([
            createLabeledField("Since", sinceInput),
            createLabeledField("Until", untilInput),
            createLabeledField("No Older Than", ageInput, "days"),
            createLabeledField("Limit", limitInput)
        ]),
        layout.filterRow([
            layout.mutedText("Domains", { tag: "span" }),
            createDomainScopeButton("All", ""),
            ...(state.snapshot?.domains ?? []).map((item) => createDomainScopeButton(item.domain, item.domain))
        ]),
        layout.actionRow([
            layout.mutedText(describeSearchScope(), { tag: "span" }),
            resetButton
        ]),
        createFlashNode(),
        createSearchResults()
    ]);
}

async function runSearch() {
    const query = state.search.query.trim();
    if (!query) {
        state.search.results = [];
        state.search.ran = false;
        setFlash("error", "Enter a search query.");
        render();
        return;
    }
    clearFlash();
    const data = await fetchJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            domain: state.search.domain || null,
            limit: clampPositiveInt(state.search.limit, 50),
            noOlderThanDays: blankToNull(state.search.noOlderThanDays),
            query,
            since: blankToNull(state.search.since),
            until: blankToNull(state.search.until)
        })
    });
    state.search.results = data.results ?? [];
    state.search.ran = true;
    render();
}

function createFeedDomains() {
    const domains = state.snapshot?.domains ?? [];
    if (!domains.length) {
        return layout.stack([
            createFlashNode(),
            layout.mutedText("No domains yet.")
        ]);
    }
    return layout.stack([
        createFlashNode(),
        createRichTable(
            ["Domain", "Entries", "Feeds", ""],
            domains.map((item) => [
                createDomainDetailButton(item.domain),
                String(item.entryCount ?? 0),
                String(item.feedCount ?? 0),
                createDeleteDomainButton(item.domain)
            ])
        )
    ]);
}

function createFeedCreateDomain() {
    const input = createLineEdit({ value: state.createDomainDraft, placeholder: "e.g. TechNews" });
    input.addEventListener("input", () => {
        state.createDomainDraft = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            submitCreateDomain().catch(handleActionError);
        }
    });
    const button = createTextButton({ text: "Create", color: "#63d9a4" });
    button.addEventListener("click", () => {
        submitCreateDomain().catch(handleActionError);
    });
    return layout.stack([
        layout.normalText("Create an empty domain so feeds and entries can be grouped the same way as KoreFeed."),
        layout.searchRow([input, button], { singleAction: true }),
        createFlashNode()
    ]);
}

async function submitCreateDomain() {
    const domain = state.createDomainDraft.trim();
    if (!domain) {
        setFlash("error", "Domain name is required.");
        render();
        return;
    }
    clearFlash();
    await fetchJson("/api/domains", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain })
    });
    state.createDomainDraft = "";
    state.search.domain = domain;
    setFlash("notice", `Created domain ${domain}.`);
    await refreshSnapshot({ rerunSearch: state.search.ran && Boolean(state.search.query.trim()) });
    render();
}

function createFeedTable() {
    const feeds = filterFeedsByDomain(state.snapshot?.feeds ?? []);
    if (!feeds.length) {
        return layout.mutedText(state.search.domain ? `No feeds in ${state.search.domain}.` : "No feeds yet.");
    }
    return createRichTable(
        ["Domain", "Feed", "Rate", "Last Run", "Status", "Content", "New", "Next"],
        feeds.map((feed) => [
            createDomainDetailButton(feed.domain ?? "default"),
            feed.title ?? "",
            formatRate(feed.updateMinutes),
            formatLastRun(feed),
            feed.lastStatus ?? "-",
            formatContentStatus(feed.contentStatus),
            String(feed.lastNewEntries ?? 0),
            formatNextRun(feed.nextRunAt)
        ])
    );
}

function createFeedEntries() {
    const items = filterEntriesByDomain(state.snapshot?.recentEntries ?? []);
    return createEntryList(items);
}

function createEntryList(items) {
    if (!items.length) {
        return layout.mutedText("No entries yet.");
    }
    return layout.itemList(items.map((item) => layout.item([
        layout.titleRow(item.title ?? "", item.feed_title || item.domain || ""),
        layout.normalText(item.summary || item.snippet || ""),
        layout.mutedText([item.author, item.published_at, item.url].filter(Boolean).join(" | "))
    ])));
}

function createFeedDetailFeeds() {
    const snapshot = state.detail.snapshot;
    const feeds = snapshot?.feeds ?? [];
    if (!feeds.length) {
        return layout.mutedText("No feeds in this domain yet.");
    }
    return layout.stack([
        layout.metadataStrip([
            ["domain", snapshot?.domain ?? state.route.domain],
            ["feeds", String(snapshot?.metrics?.feeds ?? 0)],
            ["entries", String(snapshot?.metrics?.entries ?? 0)]
        ]),
        createRichTable(
            ["Name", "Rate", "Content", "Entries", "Next", "Actions"],
            feeds.map((feed) => [
                createFeedTitleCell(feed),
                createFeedRateEditor(feed),
                formatContentStatus(feed.contentStatus),
                String(feed.entryCount ?? 0),
                formatNextRun(feed.nextRunAt),
                createFeedActions(feed)
            ])
        )
    ]);
}

function createFeedDetailEntries() {
    const snapshot = state.detail.snapshot;
    const items = state.detail.search.ran ? state.detail.search.results : (snapshot?.entries ?? []);
    const input = createLineEdit({ value: state.detail.search.query, placeholder: `search ${state.route.domain}...` });
    input.addEventListener("input", () => {
        state.detail.search.query = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            runDetailSearch().catch(handleActionError);
        }
    });
    const button = createTextButton({ text: "Search", color: "#63d9a4" });
    button.addEventListener("click", () => {
        runDetailSearch().catch(handleActionError);
    });
    const resetButton = createTextButton({ text: "Reset", color: "#f6c177" });
    resetButton.addEventListener("click", resetDetailSearch);
    return layout.stack([
        layout.actionRow([
            layout.metadataStrip([
                ["entries", String(snapshot?.metrics?.entries ?? 0)],
                ["view", state.detail.search.ran ? "search" : "latest"]
            ]),
            resetButton
        ]),
        layout.searchRow([input, button], { singleAction: true }),
        items.length
            ? createRichTable(
                ["#", "Headline", "Feed", "Published", ""],
                items.map((item) => [
                    String(item.id ?? ""),
                    createEntryTitleCell(item),
                    item.feed_title || "-",
                    formatTimestamp(item.published_at || item.updated_at),
                    createDeleteEntryButton(item.id)
                ])
            )
            : layout.mutedText(state.detail.search.ran ? "No matching entries." : "No entries yet.")
    ]);
}

function createFeedDetailAddFeed() {
    const titleInput = createLineEdit({ value: state.detail.addFeed.title, placeholder: "Feed name" });
    titleInput.addEventListener("input", () => {
        state.detail.addFeed.title = titleInput.value;
    });
    const urlInput = createLineEdit({ value: state.detail.addFeed.url, placeholder: "https://.../feed" });
    urlInput.addEventListener("input", () => {
        state.detail.addFeed.url = urlInput.value;
    });
    const rateInput = createTypedInput({ type: "number", value: state.detail.addFeed.updateMinutes, placeholder: "60", min: "1" });
    rateInput.addEventListener("input", () => {
        state.detail.addFeed.updateMinutes = rateInput.value;
    });
    const notesInput = createLineEdit({ value: state.detail.addFeed.notes, placeholder: "Optional notes" });
    notesInput.addEventListener("input", () => {
        state.detail.addFeed.notes = notesInput.value;
    });
    const addButton = createTextButton({ text: "Add Feed", color: "#63d9a4" });
    addButton.addEventListener("click", () => {
        submitDetailFeed().catch(handleActionError);
    });
    return layout.stack([
        layout.formGrid([
            createLabeledField("Feed name", titleInput),
            createLabeledField("Feed URL", urlInput),
            createLabeledField("Update rate (minutes)", rateInput),
            createLabeledField("Notes", notesInput)
        ]),
        layout.actionRow([
            layout.mutedText(`New feeds will be added to ${state.route.domain}.`, { tag: "span" }),
            addButton
        ])
    ]);
}

function createFeedDetailManageEntries() {
    const feeds = state.detail.snapshot?.feeds ?? [];
    if (!feeds.length) {
        return layout.mutedText("Add a feed before managing entries.");
    }
    return layout.stack([
        layout.normalText("Delete saved entries feed by feed inside this domain."),
        ...feeds.map((feed) => layout.splitActionRow(
            `${feed.title ?? "Feed"} (${feed.entryCount ?? 0})`,
            createDeleteFeedEntriesButton(feed)
        ))
    ]);
}

function createFeedDetailSettings() {
    const settings = state.detail.settings;
    const daysInput = createTypedInput({ type: "number", value: settings.days, placeholder: "30", min: "1" });
    daysInput.addEventListener("input", () => {
        state.detail.settings.days = daysInput.value;
    });
    const startInput = createTypedInput({ type: "date", value: settings.startDate, placeholder: "Start" });
    startInput.addEventListener("input", () => {
        state.detail.settings.startDate = startInput.value;
    });
    const endInput = createTypedInput({ type: "date", value: settings.endDate, placeholder: "End" });
    endInput.addEventListener("input", () => {
        state.detail.settings.endDate = endInput.value;
    });
    const saveButton = createTextButton({ text: "Save Changes", color: "#63d9a4" });
    saveButton.addEventListener("click", () => {
        saveDetailSettings().catch(handleActionError);
    });
    return layout.stack([
        layout.filterRow([
            createSettingsModeButton("No limit", "none"),
            createSettingsModeButton("Days previous", "days_previous"),
            createSettingsModeButton("Calendar period", "calendar_period")
        ]),
        settings.mode === "days_previous"
            ? createLabeledField("Days", daysInput)
            : null,
        settings.mode === "calendar_period"
            ? layout.formGrid([
                createLabeledField("Start", startInput),
                createLabeledField("End", endInput)
            ])
            : null,
        layout.actionRow([
            layout.mutedText("Stored per-domain feed age settings.", { tag: "span" }),
            saveButton
        ])
    ].filter(Boolean));
}

function createSearchResults() {
    if (!state.search.ran) {
        return layout.mutedText("Results appear here after a search.");
    }
    if (!state.search.results.length) {
        return layout.mutedText("No results.");
    }
    return createEntryList(state.search.results);
}

function createTypedInput(spec) {
    return el("input", {
        className: "line-edit font-normal",
        attrs: {
            min: spec.min,
            placeholder: spec.placeholder ?? "",
            type: spec.type ?? "text",
            value: spec.value ?? ""
        }
    });
}

function createLabeledField(label, input, suffix = "") {
    return layout.stack([
        layout.mutedText(label, { tag: "span" }),
        input,
        suffix ? layout.mutedText(suffix, { tag: "span" }) : null
    ].filter(Boolean));
}

function createDomainScopeButton(label, domain) {
    const active = (state.search.domain || "") === domain;
    const button = createTextButton({ text: label, color: active ? "#63d9a4" : "#78b0ff" });
    button.addEventListener("click", () => {
        selectDomain(domain).catch(handleActionError);
    });
    return button;
}

function createDomainRowButton(domain) {
    const button = createTextButton({ text: domain, color: domain === state.search.domain ? "#63d9a4" : "#78b0ff" });
    button.addEventListener("click", () => {
        selectDomain(domain).catch(handleActionError);
    });
    return button;
}

function createDomainDetailButton(domain) {
    const button = createTextButton({ text: domain, color: "#78b0ff" });
    button.addEventListener("click", () => {
        window.location.href = `/ui/feeds/${encodeURIComponent(domain)}`;
    });
    return button;
}

function createDeleteDomainButton(domain) {
    const button = createTextButton({ text: "Delete", color: "#ff8fab" });
    button.addEventListener("click", () => {
        deleteDomain(domain).catch(handleActionError);
    });
    return button;
}

function createRichTable(headers, rows) {
    return el("table", { className: "layout-table" }, [
        el("thead", {}, [
            el("tr", {}, headers.map((header) => el("th", { text: header })))
        ]),
        el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell) => el("td", {}, [coerceCell(cell)])))))
    ]);
}

function coerceCell(cell) {
    if (cell instanceof Node) {
        return cell;
    }
    return el("span", { className: "font-normal", text: cell ?? "" });
}

function describeSearchScope() {
    if (state.search.domain) {
        return `Scoped to ${state.search.domain}.`;
    }
    return "Searching across all domains.";
}

function createFeedTitleCell(feed) {
    return layout.stack([
        feed.url
            ? el("a", {
                className: "font-normal",
                text: feed.title ?? "",
                attrs: {
                    href: feed.url,
                    rel: "noreferrer",
                    target: "_blank"
                }
            })
            : el("span", { className: "font-normal", text: feed.title ?? "" }),
        layout.mutedText([formatLastRun(feed), feed.lastStatus || ""].filter(Boolean).join(" | "))
    ]);
}

function createFeedRateEditor(feed) {
    const input = createTypedInput({
        type: "number",
        value: state.detail.feedRateDrafts[feed.id] ?? String(feed.updateMinutes ?? 60),
        placeholder: "60",
        min: "1"
    });
    input.addEventListener("input", () => {
        state.detail.feedRateDrafts[feed.id] = input.value;
    });
    const button = createTextButton({ text: "Save", color: "#63d9a4" });
    button.addEventListener("click", () => {
        saveFeedRate(feed.id).catch(handleActionError);
    });
    return layout.controlRow([input, button]);
}

function createFeedActions(feed) {
    return layout.controlRow([
        createDeleteFeedEntriesButton(feed),
        createDeleteFeedButton(feed.id)
    ]);
}

function createDeleteFeedButton(feedId) {
    const button = createTextButton({ text: "Delete", color: "#ff8fab" });
    button.addEventListener("click", () => {
        deleteFeed(feedId).catch(handleActionError);
    });
    return button;
}

function createDeleteFeedEntriesButton(feed) {
    const button = createTextButton({ text: "Delete Entries", color: "#f6c177" });
    button.addEventListener("click", () => {
        deleteEntriesForFeed(feed).catch(handleActionError);
    });
    return button;
}

function createEntryTitleCell(item) {
    if (item.url) {
        return el("a", {
            className: "font-normal",
            text: item.title ?? "",
            attrs: {
                href: item.url,
                rel: "noreferrer",
                target: "_blank"
            }
        });
    }
    return el("span", { className: "font-normal", text: item.title ?? "" });
}

function createDeleteEntryButton(entryId) {
    const button = createTextButton({ text: "Delete", color: "#ff8fab" });
    button.addEventListener("click", () => {
        deleteEntry(entryId).catch(handleActionError);
    });
    return button;
}

function createSettingsModeButton(label, mode) {
    const active = state.detail.settings.mode === mode;
    const button = createTextButton({ text: label, color: active ? "#63d9a4" : "#78b0ff" });
    button.addEventListener("click", () => {
        state.detail.settings.mode = mode;
        render();
    });
    return button;
}

function createFlashNode() {
    if (state.flash.error) {
        return layout.errorText(state.flash.error);
    }
    if (state.flash.notice) {
        return layout.mutedText(state.flash.notice);
    }
    return null;
}

function clearFlash() {
    state.flash = { error: "", notice: "" };
}

function setFlash(kind, message) {
    state.flash = {
        error: kind === "error" ? message : "",
        notice: kind === "notice" ? message : ""
    };
}

function resetSearch() {
    clearFlash();
    state.search = {
        ...state.search,
        domain: "",
        limit: "50",
        noOlderThanDays: "",
        query: "",
        results: [],
        ran: false,
        since: "",
        until: ""
    };
    render();
}

async function selectDomain(domain) {
    clearFlash();
    state.search.domain = domain;
    if (state.search.ran && state.search.query.trim()) {
        await runSearch();
        return;
    }
    render();
}

async function deleteDomain(domain) {
    if (!window.confirm(`Delete domain ${domain}? This removes its feeds and entries.`)) {
        return;
    }
    clearFlash();
    const response = await fetch(`/api/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
    if (!response.ok) {
        throw new Error(await readErrorMessage(response, `Could not delete domain ${domain}.`));
    }
    if (state.search.domain === domain) {
        state.search.domain = "";
    }
    setFlash("notice", `Deleted domain ${domain}.`);
    await refreshSnapshot({ rerunSearch: state.search.ran && Boolean(state.search.query.trim()) });
    render();
}

async function refreshSnapshot(options = {}) {
    state.snapshot = await fetchJson("/api/snapshot");
    if (state.search.domain && !(state.snapshot.domains ?? []).some((item) => item.domain === state.search.domain)) {
        state.search.domain = "";
    }
    if (options.rerunSearch && state.search.query.trim()) {
        const data = await fetchJson("/api/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                domain: state.search.domain || null,
                limit: clampPositiveInt(state.search.limit, 50),
                noOlderThanDays: blankToNull(state.search.noOlderThanDays),
                query: state.search.query,
                since: blankToNull(state.search.since),
                until: blankToNull(state.search.until)
            })
        });
        state.search.results = data.results ?? [];
    }
}

async function refreshDomainSnapshot(options = {}) {
    const snapshot = await fetchJson(`/api/domains/${encodeURIComponent(state.route.domain)}/snapshot?limit=100`);
    state.detail.snapshot = snapshot;
    state.detail.feedRateDrafts = Object.fromEntries((snapshot.feeds ?? []).map((feed) => [feed.id, String(feed.updateMinutes ?? 60)]));
    state.detail.settings = {
        days: String(snapshot.settings?.days ?? 30),
        endDate: snapshot.settings?.endDate ?? "",
        mode: snapshot.settings?.mode ?? "days_previous",
        startDate: snapshot.settings?.startDate ?? ""
    };
    if (options.rerunSearch && state.detail.search.query.trim()) {
        const data = await fetchJson("/api/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                domain: state.route.domain,
                limit: 100,
                query: state.detail.search.query
            })
        });
        state.detail.search.results = data.results ?? [];
    }
}

async function runDetailSearch() {
    const query = state.detail.search.query.trim();
    if (!query) {
        state.detail.search.results = [];
        state.detail.search.ran = false;
        render();
        return;
    }
    clearFlash();
    const data = await fetchJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            domain: state.route.domain,
            limit: 100,
            query
        })
    });
    state.detail.search.results = data.results ?? [];
    state.detail.search.ran = true;
    render();
}

function resetDetailSearch() {
    state.detail.search = {
        query: "",
        ran: false,
        results: []
    };
    render();
}

async function submitDetailFeed() {
    const payload = {
        domain: state.route.domain,
        notes: blankToNull(state.detail.addFeed.notes),
        title: state.detail.addFeed.title,
        updateMinutes: clampPositiveInt(state.detail.addFeed.updateMinutes, 60),
        url: state.detail.addFeed.url
    };
    clearFlash();
    await fetchJson("/api/feeds", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
    state.detail.addFeed = {
        notes: "",
        title: "",
        updateMinutes: "60",
        url: ""
    };
    setFlash("notice", `Added feed to ${state.route.domain}.`);
    await refreshDomainSnapshot();
    render();
}

async function saveFeedRate(feedId) {
    clearFlash();
    await fetchJson(`/api/feeds/${feedId}/rate`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updateMinutes: clampPositiveInt(state.detail.feedRateDrafts[feedId], 60) })
    });
    setFlash("notice", "Saved feed rate.");
    await refreshDomainSnapshot();
    render();
}

async function deleteFeed(feedId) {
    if (!window.confirm("Delete this feed?")) {
        return;
    }
    clearFlash();
    const response = await fetch(`/api/feeds/${feedId}`, { method: "DELETE" });
    if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Could not delete feed."));
    }
    setFlash("notice", "Deleted feed.");
    await refreshDomainSnapshot({ rerunSearch: state.detail.search.ran && Boolean(state.detail.search.query.trim()) });
    render();
}

async function deleteEntriesForFeed(feed) {
    if (!window.confirm(`Delete saved entries for ${feed.title}?`)) {
        return;
    }
    clearFlash();
    const response = await fetch(`/api/feeds/${feed.id}/entries`, { method: "DELETE" });
    if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Could not delete feed entries."));
    }
    setFlash("notice", `Deleted entries for ${feed.title}.`);
    await refreshDomainSnapshot({ rerunSearch: state.detail.search.ran && Boolean(state.detail.search.query.trim()) });
    render();
}

async function deleteEntry(entryId) {
    if (!window.confirm("Delete this entry?")) {
        return;
    }
    clearFlash();
    const response = await fetch(`/api/entries/${entryId}`, { method: "DELETE" });
    if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Could not delete entry."));
    }
    setFlash("notice", `Deleted entry ${entryId}.`);
    await refreshDomainSnapshot({ rerunSearch: state.detail.search.ran && Boolean(state.detail.search.query.trim()) });
    render();
}

async function saveDetailSettings() {
    const payload = {
        days: state.detail.settings.mode === "days_previous" ? clampPositiveInt(state.detail.settings.days, 30) : null,
        endDate: blankToNull(state.detail.settings.endDate),
        mode: state.detail.settings.mode,
        startDate: blankToNull(state.detail.settings.startDate)
    };
    clearFlash();
    await fetchJson(`/api/domains/${encodeURIComponent(state.route.domain)}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
    setFlash("notice", "Saved domain settings.");
    await refreshDomainSnapshot();
    render();
}

function filterFeedsByDomain(items) {
    if (!state.search.domain) {
        return items;
    }
    return items.filter((item) => item.domain === state.search.domain);
}

function filterEntriesByDomain(items) {
    if (!state.search.domain) {
        return items;
    }
    return items.filter((item) => item.domain === state.search.domain);
}

function formatRate(value) {
    const minutes = clampPositiveInt(value, 60);
    return `${minutes}m`;
}

function formatLastRun(feed) {
    const stamp = formatTimestamp(feed.lastRunAt);
    if (!stamp) {
        return "-";
    }
    const duration = feed.lastDurationSec ? ` (${feed.lastDurationSec}s)` : "";
    return `${stamp}${duration}`;
}

function formatContentStatus(value) {
    if (!value) {
        return "-";
    }
    return String(value).replaceAll("_", " ");
}

function formatNextRun(value) {
    if (!value) {
        return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return formatTimestamp(value);
    }
    const deltaMs = date.getTime() - Date.now();
    if (deltaMs > 0) {
        const minutes = Math.round(deltaMs / 60000);
        return `in ${minutes}m`;
    }
    return formatTimestamp(value);
}

function formatTimestamp(value) {
    if (!value) {
        return "";
    }
    return String(value).replace("T", " ").replace("Z", "").slice(0, 16);
}

function clampPositiveInt(value, fallback) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    if (Number.isNaN(parsed) || parsed <= 0) {
        return fallback;
    }
    return parsed;
}

function blankToNull(value) {
    const text = String(value ?? "").trim();
    return text || null;
}

function parseRoute() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    if (parts.length === 3 && parts[0] === "ui" && parts[1] === "feeds") {
        return {
            kind: "detail",
            domain: decodeURIComponent(parts[2])
        };
    }
    return {
        kind: "dashboard",
        domain: ""
    };
}

async function readErrorMessage(response, fallback) {
    const text = (await response.text()).trim();
    if (!text) {
        return fallback;
    }
    const bodyStart = text.indexOf("<body>");
    if (bodyStart >= 0) {
        return fallback;
    }
    return text;
}

function handleActionError(error) {
    setFlash("error", error.message);
    render();
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([layout.errorText(error.message)]));
});
