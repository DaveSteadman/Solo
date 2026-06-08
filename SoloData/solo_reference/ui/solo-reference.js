// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders the SoloReference dashboard plus HTML article read/edit routes.

import { createPage } from "/common/framework/js/basic-layout.js";
import { createCheckbox } from "/common/framework/js/checkbox.js";
import { el } from "/common/framework/js/dom.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextArea } from "/common/framework/js/text-area.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { formatNumber, stripHtml } from "/common/framework/js/text-utils.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
const state = {
    article: null,
    articleError: "",
    articleLinks: [],
    edit: {
        body: "",
        summary: "",
        title: ""
    },
    importForm: {
        delaySeconds: "1.0",
        limit: "200",
        maxDepth: "1",
        resume: true,
        seedUrl: ""
    },
    flash: {
        error: "",
        notice: ""
    },
    pageSpec: null,
    route: parseRoute(),
    search: {
        query: "",
        ran: false,
        results: []
    },
    snapshot: null
};

let importPollTimer = null;

const previewRefs = {
    body: null,
    summary: null
};

const BODY_HTML_PLACEHOLDER = [
    "<h2>Overview</h2>",
    "<p>Start the article with clear HTML paragraphs.</p>",
    "",
    "<h2>Related Articles</h2>",
    "<p>",
    "  Link to another article with a standard anchor:",
    "  <a href=\"Example Article\">Example Article</a>",
    "</p>",
    "",
    "<ul>",
    "  <li>Use headings, paragraphs, lists and links.</li>",
    "  <li>Markdown is not supported.</li>",
    "</ul>"
].join("\n");

const controlFactories = {
    iconTextButton: createActionIconTextButton,
    referenceImportForm: createReferenceImportForm,
    referenceImportProgress: createReferenceImportProgress,
    referenceArticleEditor: createReferenceArticleEditor,
    referenceArticlePreview: createReferenceArticlePreview,
    referenceArticleRead: createReferenceArticleRead,
    referenceArticles: createReferenceArticles,
    referencePaths: createReferencePaths,
    referenceSearch: createReferenceSearch,
    referenceStatus: createReferenceStatus
};

async function boot() {
    state.pageSpec = await fetchJson("/ui/page.json");
    await refreshForRoute();
    render();
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw new Error(await readErrorMessage(response, `${url} returned HTTP ${response.status}`));
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

async function readErrorMessage(response, fallback) {
    try {
        const text = await response.text();
        if (!text) {
            return fallback;
        }
        try {
            const payload = JSON.parse(text);
            if (typeof payload?.detail === "string" && payload.detail.trim()) {
                return payload.detail;
            }
        } catch {
            // Use the original text when the response body is not JSON.
        }
        return text;
    } catch {
        return fallback;
    }
}

function render() {
    mount.replaceChildren(createPage(buildPageSpec(), createControl));
    syncImportPolling();
}

function createControl(spec) {
    const factory = controlFactories[spec.type];
    if (!factory) {
        throw new Error(`Unknown component type: ${spec.type}`);
    }
    return factory(spec);
}

function buildPageSpec() {
    const spec = structuredClone(state.pageSpec);
    spec.header = buildHeader(spec.header ?? {});
    if (state.route.kind === "home") {
        return spec;
    }
    if (state.route.kind === "import") {
        return {
            ...spec,
            content: [
                {
                    type: "panels",
                    columns: 2,
                    items: [
                        {
                            title: "Crawl From Seed URL",
                            items: [{ type: "referenceImportForm" }]
                        },
                        {
                            title: "Progress",
                            items: [{ type: "referenceImportProgress" }]
                        }
                    ]
                }
            ]
        };
    }
    if (state.route.kind === "read") {
        return {
            ...spec,
            content: [
                {
                    type: "panel",
                    title: "Article",
                    items: [{ type: "referenceArticleRead" }]
                }
            ]
        };
    }
    return {
        ...spec,
        content: [
            {
                type: "panels",
                columns: 2,
                items: [
                    {
                        title: state.route.kind === "new" ? "New Article" : "Edit Article",
                        items: [{ type: "referenceArticleEditor" }]
                    },
                    {
                        title: "HTML Preview",
                        items: [{ type: "referenceArticlePreview" }]
                    }
                ]
            }
        ]
    };
}

function buildHeader(baseHeader) {
    if (state.route.kind === "home") {
        return {
            ...baseHeader,
            actions: [
                { type: "iconTextButton", icon: "preview", text: "Refresh", color: "#f6c177", action: "refresh" },
                { type: "iconTextButton", icon: "action", text: "Import", color: "#78b0ff", action: "goImport" },
                { type: "iconTextButton", icon: "action", text: "New Article", color: "#63d9a4", action: "newArticle" }
            ]
        };
    }
    if (state.route.kind === "import") {
        return {
            title: "SoloReference / Import",
            subtitle: "Kiwix seed crawl and progress monitor",
            actions: [
                { type: "iconTextButton", icon: "reference", text: "Reference", color: "#78b0ff", action: "goHome" },
                { type: "iconTextButton", icon: "preview", text: "Refresh", color: "#f6c177", action: "refresh" }
            ]
        };
    }
    if (state.route.kind === "read") {
        return {
            title: `SoloReference / ${state.article?.title ?? state.route.title}`,
            subtitle: "HTML article reader",
            actions: [
                { type: "iconTextButton", icon: "reference", text: "Reference", color: "#78b0ff", action: "goHome" },
                { type: "iconTextButton", icon: "action", text: "Edit", color: "#f6c177", action: `edit:${state.article?.title ?? state.route.title}`, disabled: !state.article },
                { type: "iconTextButton", icon: "action", text: "New Article", color: "#63d9a4", action: "newArticle" },
                { type: "iconTextButton", icon: "preview", text: "Refresh", color: "#b7bdf8", action: "refresh" }
            ]
        };
    }
    return {
        title: state.route.kind === "new" ? "SoloReference / New Article" : `SoloReference / Edit / ${state.article?.title ?? state.route.title}`,
        subtitle: "Create or update HTML reference articles",
        actions: [
            { type: "iconTextButton", icon: "reference", text: "Reference", color: "#78b0ff", action: "goHome" },
            ...(state.route.kind === "edit" ? [{ type: "iconTextButton", icon: "preview", text: "View", color: "#f6c177", action: `view:${state.article?.title ?? state.route.title}`, disabled: !state.article }] : []),
            { type: "iconTextButton", icon: "action", text: "New Article", color: "#63d9a4", action: "newArticle" },
            { type: "iconTextButton", icon: "preview", text: "Refresh", color: "#b7bdf8", action: "refresh" }
        ]
    };
}

function createActionIconTextButton(spec) {
    const button = createIconTextButton(spec);
    button.disabled = Boolean(spec.disabled);
    wireAction(button, spec.action);
    return button;
}

function createActionTextButton(spec) {
    const button = createTextButton(spec);
    button.disabled = Boolean(spec.disabled);
    wireAction(button, spec.action);
    return button;
}

function wireAction(button, action) {
    if (!action) {
        return;
    }
    button.dataset.action = action;
    button.addEventListener("click", () => {
        runAction(action).catch((error) => {
            state.flash.error = error.message;
            render();
        });
    });
}

async function runAction(action) {
    state.flash.error = "";
    if (action === "refresh") {
        await refreshForRoute({ preserveDraft: true });
        render();
        return;
    }
    if (action === "goHome") {
        navigateTo("/ui");
        return;
    }
    if (action === "newArticle") {
        navigateTo("/ui/articles/new");
        return;
    }
    if (action === "goImport") {
        navigateTo("/ui/import");
        return;
    }
    if (action === "saveArticle") {
        await saveArticle();
        return;
    }
    if (action === "startImportCrawl") {
        await startImportCrawl();
        return;
    }
    if (action === "stopImport") {
        await stopImport();
        return;
    }
    if (action === "cancelEdit") {
        if (state.route.kind === "edit" && state.article?.title) {
            navigateTo(articleViewPath(state.article.title));
            return;
        }
        navigateTo("/ui");
        return;
    }
    if (action.startsWith("view:")) {
        navigateTo(articleViewPath(action.slice("view:".length)));
        return;
    }
    if (action.startsWith("edit:")) {
        navigateTo(articleEditPath(action.slice("edit:".length)));
        return;
    }
}

async function refreshForRoute(options = {}) {
    state.snapshot = await fetchJson("/api/snapshot");
    state.articleError = "";
    if (state.route.kind === "read" || state.route.kind === "edit") {
        await loadArticle(state.route.title, state.route.kind === "edit" && !options.preserveDraft);
        return;
    }
    if (state.route.kind === "import") {
        return;
    }
    if (state.route.kind === "new" && !options.preserveDraft) {
        resetDraft();
    }
}

async function loadArticle(title, fillDraft) {
    try {
        const [article, articleLinks] = await Promise.all([
            fetchJson(`/api/articles/${encodeURIComponent(title)}`),
            fetchJson(`/api/articles/${encodeURIComponent(title)}/links`).catch(() => [])
        ]);
        state.article = article;
        state.articleLinks = Array.isArray(articleLinks) ? articleLinks : [];
        if (fillDraft) {
            syncDraftFromArticle(state.article);
        }
    } catch (error) {
        state.article = null;
        state.articleLinks = [];
        state.articleError = error.message;
        if (fillDraft) {
            resetDraft();
        }
    }
}

function resetDraft() {
    state.edit = {
        body: "",
        summary: "",
        title: ""
    };
}

function syncDraftFromArticle(article) {
    state.edit = {
        body: String(article?.body ?? ""),
        summary: String(article?.summary ?? ""),
        title: String(article?.title ?? "")
    };
}

function navigateTo(url) {
    window.location.href = url;
}

function parseRoute() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    if (parts[0] !== "ui") {
        return { kind: "home" };
    }
    if (parts[1] === "articles" && parts[2] === "new") {
        return { kind: "new" };
    }
    if (parts[1] === "import") {
        return { kind: "import" };
    }
    if (parts[1] === "articles" && parts[2] && parts[3] === "edit") {
        return { kind: "edit", title: decodeURIComponent(parts[2]) };
    }
    if (parts[1] === "articles" && parts[2]) {
        return { kind: "read", title: decodeURIComponent(parts[2]) };
    }
    return { kind: "home" };
}

function articleViewPath(title) {
    return `/ui/articles/${encodeURIComponent(title)}`;
}

function articleEditPath(title) {
    return `/ui/articles/${encodeURIComponent(title)}/edit`;
}

// MARK: Root dashboard

function createReferenceStatus() {
    const metrics = state.snapshot?.metrics ?? {};
    return layout.metricGrid([
        ["Articles", metrics.articles ?? 0],
        ["Redirects", metrics.redirects ?? 0],
        ["Links", metrics.links ?? 0],
        ["Uptime", `${state.snapshot?.uptimeSec ?? 0}s`]
    ]);
}

function createReferencePaths() {
    const paths = state.snapshot?.paths ?? {};
    return layout.pathList([
        ["Reference", paths.referenceRoot ?? ""],
        ["Database", paths.db ?? ""],
        ["Logs", paths.logs ?? ""]
    ]);
}

function createReferenceSearch() {
    const input = createLineEdit({ value: state.search.query, placeholder: "Search articles" });
    input.addEventListener("input", () => {
        state.search.query = input.value;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            runSearch().catch(handleActionError);
        }
    });
    const button = createTextButton({ text: "Search", color: "#f6c177" });
    button.addEventListener("click", () => {
        runSearch().catch(handleActionError);
    });
    return layout.stack([
        layout.normalText("Bare terms use AND by default. Use quotes for phrases, OR or | for alternatives, NOT to exclude, and parentheses to group."),
        layout.searchRow([input, button], { singleAction: true }),
        createFlashBlock(),
        createArticleList(state.search.results, {
            emptyText: state.search.ran ? "No results." : "Results appear here after a search."
        })
    ].filter(Boolean));
}

async function runSearch() {
    const trimmed = state.search.query.trim();
    if (!trimmed) {
        state.search.results = [];
        state.search.ran = false;
        render();
        return;
    }
    const data = await fetchJson(`/api/search?q=${encodeURIComponent(trimmed)}&limit=25`);
    state.search.results = data.results ?? [];
    state.search.ran = true;
    render();
}

function createReferenceArticles() {
    return layout.stack([
        layout.controlRow([
            createActionTextButton({ text: "Import", color: "#78b0ff", action: "goImport" }),
            createActionTextButton({ text: "New Article", color: "#63d9a4", action: "newArticle" })
        ]),
        createArticleList(state.snapshot?.recentArticles ?? [], { emptyText: "No articles yet." })
    ]);
}

// MARK: Import page

function createReferenceImportForm() {
    const importState = state.snapshot?.import ?? {};
    const seedUrl = createLineEdit({
        value: state.importForm.seedUrl,
        placeholder: "http://host/viewer#zim/Article or http://host/zim/A/Article_Title"
    });
    seedUrl.addEventListener("input", () => {
        state.importForm.seedUrl = seedUrl.value;
    });

    const maxDepth = createNumberEdit(state.importForm.maxDepth, { min: "0", max: "5", step: "1" });
    maxDepth.addEventListener("input", () => {
        state.importForm.maxDepth = maxDepth.value;
        depthHint.textContent = describeDepth(maxDepth.value);
    });

    const limit = createNumberEdit(state.importForm.limit, { min: "1", step: "1" });
    limit.addEventListener("input", () => {
        state.importForm.limit = limit.value;
    });

    const delay = createNumberEdit(state.importForm.delaySeconds, { min: "0.1", max: "10", step: "0.1" });
    delay.addEventListener("input", () => {
        state.importForm.delaySeconds = delay.value;
    });

    const resume = createCheckbox({ text: "Skip already-imported", checked: state.importForm.resume, color: "#78b0ff" });
    const resumeInput = resume.querySelector("input");
    resumeInput.addEventListener("change", () => {
        state.importForm.resume = resumeInput.checked;
    });

    const depthHint = layout.mutedText(describeDepth(state.importForm.maxDepth), { tag: "span" });

    return layout.form([
        layout.normalText("Paste a Kiwix URL. The importer fetches that article then follows its wikilinks up to the chosen depth."),
        layout.errorText("Depth 2+ can queue thousands of articles. Use the limit."),
        createLabeledField("Seed URL *", seedUrl),
        layout.formGrid([
            createLabeledField("Max depth", maxDepth, null),
            createLabeledField("Article limit", limit, null)
        ]),
        depthHint,
        createLabeledField("Time between imports (seconds)", delay),
        resume,
        createFlashBlock(),
        layout.controlRow([
            createActionTextButton({
                text: importState.running ? "Import Running" : "Start Crawl",
                color: "#63d9a4",
                action: "startImportCrawl",
                disabled: Boolean(importState.running)
            })
        ])
    ], { fill: true });
}

function createReferenceImportProgress() {
    const importState = state.snapshot?.import ?? {};
    if (!importState.running) {
        return layout.stack([
            layout.mutedText("No import running."),
            importState.last_error ? layout.errorText(importState.last_error) : null
        ].filter(Boolean));
    }

    return layout.stack([
        layout.metricGrid([
            ["Mode", String(importState.mode ?? "crawl")],
            ["Done", formatNumber(importState.done ?? 0)],
            ["Total", formatNumber(importState.total ?? 0)],
            ["Errors", formatNumber(importState.errors ?? 0)]
        ]),
        createProgressBar(importState),
        importState.seed ? layout.normalText(`Seed: ${importState.seed}`) : null,
        importState.delay_seconds !== undefined ? layout.normalText(`Delay: ${importState.delay_seconds}s`) : null,
        importState.last_redirect ? layout.mutedText(`Last redirect: ${importState.last_redirect}`) : null,
        importState.redirects_stored ? layout.mutedText(`Redirects stored: ${formatNumber(importState.redirects_stored)}`) : null,
        importState.last_error ? layout.errorText(importState.last_error) : null,
        layout.controlRow([
            createActionTextButton({ text: "Stop", color: "#f6c177", action: "stopImport" }),
            createActionTextButton({ text: "Refresh", color: "#78b0ff", action: "refresh" })
        ])
    ].filter(Boolean));
}

function createArticleList(items, options = {}) {
    if (!items.length) {
        return layout.mutedText(options.emptyText ?? "No articles yet.");
    }
    return layout.itemList(items.map(createArticleCard));
}

function createArticleCard(item) {
    const summary = stripHtml(item.summary || item.snippet || "").trim();
    return layout.item([
        layout.titleRow(item.title ?? "", formatNumber(item.word_count ?? 0)),
        summary ? layout.normalText(summary) : layout.mutedText("No summary."),
        layout.controlRow([
            createActionTextButton({ text: "Read", color: "#78b0ff", action: `view:${item.title}` }),
            createActionTextButton({ text: "Edit", color: "#f6c177", action: `edit:${item.title}` })
        ])
    ]);
}

// MARK: Article read page

function createReferenceArticleRead() {
    if (state.articleError) {
        return layout.stack([
            layout.errorText(state.articleError),
            layout.controlRow([
                createActionTextButton({ text: "Reference", color: "#78b0ff", action: "goHome" }),
                createActionTextButton({ text: "New Article", color: "#63d9a4", action: "newArticle" })
            ])
        ]);
    }
    if (!state.article) {
        return layout.mutedText("Loading article...");
    }
    const facts = Array.isArray(state.article.facts) ? state.article.facts : [];
    const sections = Array.isArray(state.article.sections) ? state.article.sections : [];
    return layout.stack([
        layout.metricGrid([
            ["Words", formatNumber(state.article.word_count ?? 0)],
            ["Facts", formatNumber(facts.length)],
            ["Sections", formatNumber(sections.length)]
        ]),
        state.article.redirected_from ? layout.mutedText(`Redirected from ${state.article.redirected_from}`) : null,
        createHtmlBlock("Summary", state.article.summary, "No summary."),
        createHtmlBlock("Body", state.article.body, "No body yet."),
        facts.length ? createFactsBlock(facts) : null
    ].filter(Boolean));
}

function createFactsBlock(facts) {
    return layout.item([
        layout.titleRow(`Facts (${facts.length})`),
        layout.itemList(facts.map((fact, index) => layout.item([
            layout.titleRow(`Fact ${index + 1}`),
            typeof fact === "string"
                ? layout.normalText(fact)
                : layout.preformatted(JSON.stringify(fact, null, 2))
        ])))
    ]);
}

function createHtmlBlock(title, html, emptyText) {
    return layout.item([
        layout.titleRow(title),
        html && String(html).trim()
            ? createRenderedHtml(html)
            : layout.mutedText(emptyText)
    ]);
}

function createRenderedHtml(html) {
    const container = el("div", { className: "font-normal" });
    container.innerHTML = renderArticleContent(String(html ?? ""));
    normalizeArticleAnchors(container);
    styleRenderedArticle(container);
    return container;
}

// MARK: Article edit and create page

function createReferenceArticleEditor() {
    const titleInput = createLineEdit({ value: state.edit.title, placeholder: "Article title" });
    applyEditorDarkTheme(titleInput);
    titleInput.disabled = state.route.kind === "edit";
    titleInput.addEventListener("input", () => {
        state.edit.title = titleInput.value;
    });
    const summaryArea = createTextArea({ value: state.edit.summary, placeholder: "<p>Short HTML summary...</p>", rows: 6 });
    applyEditorDarkTheme(summaryArea);
    summaryArea.addEventListener("input", () => {
        state.edit.summary = summaryArea.value;
        syncPreview();
    });
    const bodyArea = createTextArea({ value: state.edit.body, placeholder: BODY_HTML_PLACEHOLDER, rows: 22, fill: true });
    applyEditorDarkTheme(bodyArea);
    bodyArea.addEventListener("input", () => {
        state.edit.body = bodyArea.value;
        syncPreview();
    });
    return layout.stack([
        createLabeledField("Title", titleInput, state.route.kind === "edit" ? "Existing article titles stay fixed on edit." : "This title becomes the article route."),
        createLabeledField("Summary HTML", summaryArea),
        createLabeledField("Body HTML", bodyArea, "HTML formatting only. Markdown is not supported. Internal article links use the article title as the href value."),
        createFlashBlock(),
        layout.controlRow([
            createActionTextButton({ text: state.route.kind === "new" ? "Create Article" : "Save Changes", color: "#63d9a4", action: "saveArticle" }),
            createActionTextButton({ text: "Cancel", color: "#f6c177", action: "cancelEdit" })
        ])
    ].filter(Boolean), { fill: true });
}

function createReferenceArticlePreview() {
    return layout.stack([
        createPreviewBlock("Summary Preview", state.edit.summary, "No summary yet.", "summary"),
        createPreviewBlock("Body Preview", state.edit.body, "No body yet.", "body")
    ]);
}

function createPreviewBlock(title, html, emptyText, key) {
    const container = el("div", { className: "layout-stack" });
    previewRefs[key] = container;
    writePreviewContent(container, html, emptyText);
    return layout.item([
        layout.titleRow(title),
        container
    ]);
}

function createLabeledField(label, control, note) {
    return layout.stack([
        layout.mutedText(label, { tag: "span" }),
        control,
        note ? layout.mutedText(note, { tag: "span" }) : null
    ].filter(Boolean));
}

async function saveArticle() {
    const title = state.edit.title.trim();
    if (!title) {
        state.flash.error = "Title is required.";
        render();
        return;
    }
    const saved = await postJson("/api/articles", {
        title,
        link_titles: extractArticleLinkTitles(state.edit.summary, state.edit.body),
        summary: blankToNull(state.edit.summary),
        body: blankToNull(state.edit.body)
    });
    state.flash.notice = state.route.kind === "new" ? "Created article." : "Saved article.";
    navigateTo(articleViewPath(saved.title ?? title));
}

async function startImportCrawl() {
    const seedUrl = state.importForm.seedUrl.trim();
    if (!seedUrl) {
        state.flash.error = "Seed URL is required.";
        render();
        return;
    }
    state.flash.error = "";
    await postJson("/api/import/kiwix/crawl", {
        seed_url: seedUrl,
        max_depth: parseInteger(state.importForm.maxDepth, 1),
        limit: parseInteger(state.importForm.limit, 200),
        delay_seconds: parseFloatValue(state.importForm.delaySeconds, 1),
        resume: Boolean(state.importForm.resume)
    });
    state.flash.notice = "Import crawl started.";
    await refreshForRoute({ preserveDraft: true });
    render();
}

async function stopImport() {
    await postJson("/api/import/stop", {});
    state.flash.notice = "Import stop requested.";
    await refreshForRoute({ preserveDraft: true });
    render();
}

// MARK: Shared helpers

function createNumberEdit(value, attrs = {}) {
    return el("input", {
        className: "line-edit font-normal",
        attrs: {
            type: "number",
            value: value ?? "",
            ...attrs
        }
    });
}

function blankToNull(value) {
    const text = String(value ?? "").trim();
    return text ? text : null;
}

function createProgressBar(importState) {
    const done = Number(importState.done ?? 0);
    const total = Number(importState.total ?? 0);
    const percent = total > 0 ? Math.min(99, Math.round((done / total) * 100)) : 0;
    return layout.stack([
        el("progress", {
            attrs: {
                max: String(Math.max(total, 1)),
                value: String(Math.min(done, Math.max(total, 1)))
            },
            style: {
                width: "100%",
                height: "1rem"
            }
        }),
        layout.mutedText(`${percent}% complete`, { tag: "span" })
    ]);
}

function describeDepth(value) {
    const depth = parseInteger(value, 1);
    if (depth <= 0) {
        return "seed only";
    }
    if (depth === 1) {
        return "seed + direct links (~50-300)";
    }
    if (depth === 2) {
        return "seed + second hop (can reach thousands)";
    }
    return "large crawl - use a strict article limit";
}

function parseInteger(value, fallback) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function parseFloatValue(value, fallback) {
    const parsed = Number.parseFloat(String(value ?? ""));
    return Number.isFinite(parsed) ? parsed : fallback;
}

function syncImportPolling() {
    const shouldPoll = state.route.kind === "import" && Boolean(state.snapshot?.import?.running);
    if (!shouldPoll) {
        if (importPollTimer) {
            window.clearInterval(importPollTimer);
            importPollTimer = null;
        }
        return;
    }
    if (importPollTimer) {
        return;
    }
    importPollTimer = window.setInterval(async () => {
        try {
            await refreshForRoute({ preserveDraft: true });
            render();
        } catch (error) {
            state.flash.error = error.message;
            render();
        }
    }, 2000);
}

function applyEditorDarkTheme(control) {
    control.style.background = "#0f1720";
    control.style.color = "#d9e7f5";
    control.style.borderColor = "#314252";
    control.style.boxShadow = "inset 0 1px 0 rgba(255, 255, 255, 0.03)";
    control.style.caretColor = "#8ecbff";
}

function extractArticleLinkTitles(...htmlBlocks) {
    const titles = new Set();
    for (const html of htmlBlocks) {
        const text = String(html ?? "").trim();
        if (!text) {
            continue;
        }
        const container = document.createElement("div");
        container.innerHTML = normalizeHtml(text);
        for (const anchor of container.querySelectorAll("a[href], a[data-article-title]")) {
            const explicitTitle = String(anchor.getAttribute("data-article-title") ?? "").trim();
            const linkedTitle = explicitTitle || extractArticleTitleFromHref(anchor.getAttribute("href"));
            if (linkedTitle) {
                titles.add(linkedTitle);
            }
        }
    }
    return [...titles];
}

function extractArticleTitleFromHref(href) {
    const text = String(href ?? "").trim();
    if (!text || text.startsWith("#")) {
        return "";
    }
    if (isInlineArticleTitleHref(text)) {
        return decodeURIComponent(text);
    }
    try {
        const url = new URL(text, window.location.origin);
        const parts = url.pathname.split("/").filter(Boolean);
        if (parts[0] === "ui" && parts[1] === "articles" && parts[2]) {
            return decodeURIComponent(parts[2]);
        }
        if (parts[0] === "articles" && parts[1]) {
            return decodeURIComponent(parts[1]);
        }
    } catch {
        return "";
    }
    return "";
}

function normalizeArticleAnchors(container) {
    for (const anchor of container.querySelectorAll("a[href]")) {
        const href = String(anchor.getAttribute("href") ?? "").trim();
        const linkedTitle = extractArticleTitleFromHref(href);
        const isResolved = linkedTitle ? hasResolvedArticleLink(linkedTitle) : true;
        anchor.style.color = isResolved ? "#8ecbff" : "#8e96a3";
        anchor.style.textDecoration = "none";
        if (!isInlineArticleTitleHref(href)) {
            continue;
        }
        anchor.setAttribute("href", articleViewPath(decodeURIComponent(href)));
    }
}

function hasResolvedArticleLink(title) {
    const target = String(title ?? "").trim().toLowerCase();
    if (!target) {
        return true;
    }
    return state.articleLinks.some((link) => String(link?.to_title ?? "").trim().toLowerCase() === target && Boolean(link?.to_id));
}

function isInlineArticleTitleHref(href) {
    return Boolean(href) && !href.startsWith("#") && !href.includes("://") && !href.startsWith("/");
}

function createFlashBlock() {
    if (!state.flash.error && !state.flash.notice) {
        return null;
    }
    return layout.stack([
        state.flash.error ? layout.errorText(state.flash.error) : null,
        state.flash.notice ? layout.normalText(state.flash.notice) : null
    ].filter(Boolean));
}

function syncPreview() {
    writePreviewContent(previewRefs.summary, state.edit.summary, "No summary yet.");
    writePreviewContent(previewRefs.body, state.edit.body, "No body yet.");
}

function writePreviewContent(container, html, emptyText) {
    if (!container) {
        return;
    }
    container.replaceChildren(
        html && String(html).trim()
            ? createRenderedHtml(html)
            : layout.mutedText(emptyText)
    );
}

function handleActionError(error) {
    state.flash.error = error.message;
    render();
}

function normalizeHtml(value) {
    const text = String(value ?? "").trim();
    if (!text) {
        return "";
    }
    if (looksLikeHtml(text)) {
        return text;
    }
    return text.split(/\n\n+/).map((block) => `<p>${escapeHtml(block).replace(/\n/g, "<br>")}</p>`).join("");
}

function renderArticleContent(value) {
    const text = String(value ?? "").trim();
    if (!text) {
        return "";
    }
    if (looksLikeHtml(text)) {
        return text;
    }
    if (looksLikeWikiMarkup(text)) {
        return renderWikiMarkup(text);
    }
    return normalizeHtml(text);
}

function looksLikeWikiMarkup(value) {
    return /\[\[[^\]]+\]\]|^={2,6}\s*.+?\s*={2,6}$|^[#*]\s+/m.test(value);
}

function renderWikiMarkup(value) {
    const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
    const html = [];
    const paragraph = [];
    const listStack = [];

    function closeParagraph() {
        if (!paragraph.length) {
            return;
        }
        html.push(`<p>${renderWikiInline(paragraph.join(" "))}</p>`);
        paragraph.length = 0;
    }

    function closeLists(targetDepth = 0) {
        while (listStack.length > targetDepth) {
            html.push(`</${listStack.pop()}>`);
        }
    }

    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) {
            closeParagraph();
            closeLists(0);
            continue;
        }

        const headingMatch = line.match(/^(={2,6})\s*(.*?)\s*\1$/);
        if (headingMatch) {
            closeParagraph();
            closeLists(0);
            const level = Math.min(6, headingMatch[1].length);
            html.push(`<h${level}>${renderWikiInline(headingMatch[2])}</h${level}>`);
            continue;
        }

        const listMatch = rawLine.match(/^([#*]+)\s+(.*)$/);
        if (listMatch) {
            closeParagraph();
            const markers = listMatch[1];
            const depth = markers.length;
            const listTag = markers.at(-1) === "#" ? "ol" : "ul";

            while (listStack.length < depth) {
                listStack.push(listTag);
                html.push(`<${listTag}>`);
            }
            while (listStack.length > depth) {
                html.push(`</${listStack.pop()}>`);
            }
            if (listStack[listStack.length - 1] !== listTag) {
                html.push(`</${listStack.pop()}>`);
                listStack.push(listTag);
                html.push(`<${listTag}>`);
            }

            html.push(`<li>${renderWikiInline(listMatch[2])}</li>`);
            continue;
        }

        closeLists(0);
        paragraph.push(line);
    }

    closeParagraph();
    closeLists(0);
    return html.join("");
}

function renderWikiInline(value) {
    let text = escapeHtml(String(value ?? ""));
    text = text.replace(/'''''(.*?)'''''/g, "<strong><em>$1</em></strong>");
    text = text.replace(/'''(.*?)'''/g, "<strong>$1</strong>");
    text = text.replace(/''(.*?)''/g, "<em>$1</em>");
    text = text.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, (_, target, label) => createWikiAnchor(target, label));
    text = text.replace(/\[\[([^\]]+)\]\]/g, (_, target) => createWikiAnchor(target, target));
    return text;
}

function createWikiAnchor(target, label) {
    const href = escapeHtml(String(target ?? "").trim());
    const text = escapeHtml(String(label ?? target ?? "").trim());
    return `<a href="${href}">${text}</a>`;
}

function styleRenderedArticle(container) {
    container.style.maxWidth = "none";
    container.style.width = "100%";
    container.style.color = "#d8e4f0";

    for (const heading of container.querySelectorAll("h2, h3, h4, h5, h6")) {
        heading.style.margin = "1.6rem 0 0.85rem";
        heading.style.color = "#f1d48a";
    }

    for (const heading of container.querySelectorAll("h2")) {
        heading.style.paddingBottom = "0.35rem";
        heading.style.borderBottom = "1px solid rgba(142, 203, 255, 0.18)";
    }

    for (const paragraph of container.querySelectorAll("p")) {
        paragraph.style.margin = "0 0 1rem";
    }

    for (const list of container.querySelectorAll("ul, ol")) {
        list.style.margin = "0.2rem 0 1rem 1.5rem";
        list.style.padding = "0";
    }

    for (const item of container.querySelectorAll("li")) {
        item.style.margin = "0.3rem 0";
    }

    for (const code of container.querySelectorAll("code")) {
        code.style.background = "rgba(94, 129, 172, 0.18)";
        code.style.color = "#cfe7ff";
        code.style.padding = "0.08rem 0.35rem";
        code.style.borderRadius = "0.35rem";
    }

    for (const pre of container.querySelectorAll("pre")) {
        pre.style.background = "#111922";
        pre.style.border = "1px solid rgba(142, 203, 255, 0.16)";
        pre.style.borderRadius = "0.6rem";
        pre.style.padding = "0.9rem 1rem";
        pre.style.overflowX = "auto";
        pre.style.color = "#d8e4f0";
    }

    for (const table of container.querySelectorAll("table")) {
        table.style.width = "100%";
        table.style.borderCollapse = "collapse";
        table.style.margin = "0.5rem 0 1.2rem";
        table.style.background = "rgba(15, 23, 32, 0.72)";
        table.style.border = "1px solid rgba(142, 203, 255, 0.16)";
        table.style.borderRadius = "0.5rem";
        table.style.overflow = "hidden";
        table.style.display = "block";
        table.style.overflowX = "auto";
    }

    for (const cell of container.querySelectorAll("th, td")) {
        cell.style.padding = "0.55rem 0.75rem";
        cell.style.borderBottom = "1px solid rgba(142, 203, 255, 0.12)";
        cell.style.textAlign = "left";
        cell.style.verticalAlign = "top";
    }

    for (const headerCell of container.querySelectorAll("th")) {
        headerCell.style.color = "#f1d48a";
        headerCell.style.background = "rgba(142, 203, 255, 0.08)";
    }
}

function looksLikeHtml(value) {
    return /<[a-z][\s\S]*>/i.test(value);
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([layout.errorText(error.message)]));
});
