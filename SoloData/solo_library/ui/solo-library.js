// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Solo contributors
//
// Purpose:
// Renders SoloLibrary route pages from ui/page.json and live library API responses.

import { createHeading, createPage } from "/common/framework/js/basic-layout.js";
import { createIconButton } from "/common/framework/js/icon-button.js";
import { createIconPanel, createIconPanelGroup } from "/common/framework/js/icon-panel.js";
import { createIconTextButton } from "/common/framework/js/icon-text-button.js";
import { createLineEdit } from "/common/framework/js/line-edit.js";
import { createTextArea } from "/common/framework/js/text-area.js";
import { createTextButton } from "/common/framework/js/text-button.js";
import { createTextLabel } from "/common/framework/js/text-label.js";
import { stripHtml } from "/common/framework/js/text-utils.js";
import { el } from "/common/framework/js/dom.js";
import * as layout from "/common/framework/js/layout-components.js";

const mount = document.querySelector("#app");
let pageMap = {};
let currentRoute = parseRoute();
let snapshot = null;
let selectedBook = null;
let message = "";

let catalogState = { limit: "50", books: [] };
let searchState = { query: new URLSearchParams(location.search).get("q") ?? "", limit: "50", results: [], searched: false };
let importDraft = emptyBookDraft();
let editDraft = emptyBookDraft();
let kiwixDraft = {
    url: "",
    zim: "",
    query: "",
    author: "",
    viewerUrl: "",
    batchUrls: "",
    language: "en",
    catalog: "local"
};
let kiwixInventory = [];
let kiwixResults = [];
let kiwixCatalog = null;
let importLog = [];

const controlFactories = {
    heading: createHeading,
    iconButton: createActionIconButton,
    iconPanel: (spec) => createIconPanel(spec, createControl),
    iconTextButton: createActionIconTextButton,
    libraryAddBook: createLibraryImport,
    libraryCatalog: createLibraryCatalog,
    libraryEditBook: createLibraryEdit,
    libraryRead: createLibraryRead,
    librarySearch: createLibrarySearch,
    lineEdit: createLineEdit,
    paragraph: (spec) => layout.normalText(spec.text),
    textButton: createActionTextButton,
    textLabel: createTextLabel
};

async function boot() {
    const spec = await fetchJson("/ui/page.json");
    pageMap = spec.pages ?? {};
    await loadRouteData();
    render();
    window.addEventListener("popstate", async () => {
        currentRoute = parseRoute();
        await loadRouteData();
        render();
    });
}

async function loadRouteData() {
    snapshot = await fetchJson("/api/snapshot");
    catalogState.books = snapshot.recentBooks ?? [];
    if (currentRoute.bookId) {
        selectedBook = await fetchJson(`/api/books/${encodeURIComponent(currentRoute.bookId)}`);
        editDraft = draftFromBook(selectedBook);
    } else {
        selectedBook = null;
    }
    if (currentRoute.page === "search" && searchState.query.trim()) {
        await searchCatalog(false);
    }
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    let body = null;
    try {
        body = await response.json();
    } catch {
        body = null;
    }
    if (!response.ok) {
        throw new Error(body?.detail || `${url} returned HTTP ${response.status}`);
    }
    return body;
}

function render() {
    const pageSpec = pageMap[currentRoute.page] ?? pageMap.catalog;
    mount.replaceChildren(createPage(resolvePageSpec(pageSpec), createControl));
}

function resolvePageSpec(spec) {
    return JSON.parse(JSON.stringify(spec), (_key, value) => {
        if (typeof value !== "string") {
            return value;
        }
        return value
            .replaceAll("{book.route_id}", selectedBook?.route_id ?? "")
            .replaceAll("{book.title}", selectedBook?.title ?? "");
    });
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
    if (action) {
        button.dataset.action = action;
        button.addEventListener("click", () => runAction(action));
    }
}

async function runAction(action) {
    if (action === "refresh") {
        await loadRouteData();
        render();
        return;
    }
    if (action === "goCatalog") {
        await navigate("/ui");
        return;
    }
    if (action === "goSearch") {
        await navigate("/ui/search");
        return;
    }
    if (action === "goImport") {
        await navigate("/ui/import");
        return;
    }
    if (action === "goReadBook" && currentRoute.bookId) {
        await navigate(`/ui/books/${encodeURIComponent(currentRoute.bookId)}`);
        return;
    }
    if (action === "goEditBook" && currentRoute.bookId) {
        await navigate(`/ui/books/${encodeURIComponent(currentRoute.bookId)}/edit`);
        return;
    }
    if (action.startsWith("readBook:")) {
        await navigate(`/ui/books/${encodeURIComponent(action.slice("readBook:".length))}`);
        return;
    }
    if (action.startsWith("editBook:")) {
        await navigate(`/ui/books/${encodeURIComponent(action.slice("editBook:".length))}/edit`);
    }
}

async function navigate(path) {
    history.pushState({}, "", path);
    currentRoute = parseRoute();
    await loadRouteData();
    render();
}

function createLibraryCatalog() {
    const metrics = snapshot?.service?.metrics ?? {};
    const limitInput = createLineEdit({ value: catalogState.limit, placeholder: "50" });
    limitInput.addEventListener("input", () => {
        catalogState.limit = limitInput.value;
    });
    const refreshButton = createTextButton({ text: "Refresh", color: "#78b0ff" });
    refreshButton.addEventListener("click", async () => {
        await loadRouteData();
        render();
    });
    const books = catalogState.books ?? [];
    return layout.stack([
        layout.grid([
            layout.metric("Catalogs", metrics.catalogs ?? 0),
            layout.metric("Books", metrics.books ?? 0),
            layout.metric("DB size", formatBytes(metrics.dbSizeBytes ?? 0))
        ]),
        layout.searchRow([limitInput, refreshButton], { compact: true }),
        books.length
            ? createIconPanelGroup(books.map(createCatalogBookCard), { minWidth: "220px" })
            : layout.mutedText("No books have been imported yet.")
    ]);
}

function createCatalogBookCard(book) {
    return createIconPanel({
        type: "iconPanel",
        icon: "library",
        color: "#78b0ff",
        overline: authorShortName(book) || "Unknown author",
        title: book.title ?? "",
        description: metadataLine(book),
        meta: [
            { label: "ID", value: book.route_id || "-" },
            { label: "Author", value: book.author || "-" },
            { label: "Words", value: book.word_count ? String(book.word_count) : "-" }
        ],
        actions: [
            { type: "textButton", text: "Read", color: "#63d9a4", action: `readBook:${book.route_id}` },
            { type: "textButton", text: "Edit", color: "#78b0ff", action: `editBook:${book.route_id}` }
        ]
    }, createControl);
}

function authorShortName(book) {
    if (book.author_short_name) {
        return book.author_short_name;
    }

    const author = String(book.author || "").trim();
    if (!author) {
        return "";
    }

    const parts = author.split(",").map((part) => part.trim()).filter(Boolean);
    if (parts.length >= 2 && !/\d/.test(parts[0])) {
        const surname = parts[0];
        const given = parts.slice(1).filter((part) => !/\d/.test(part));
        if (given.length) {
            return `${given.join(" ")} ${surname}`.trim();
        }
        return surname;
    }

    return author
        .replace(/,?\s*\d{2,4}\??\s*(?:BCE|CE|BC|AD)?\s*[-â€“]\s*\d{2,4}\??\s*(?:BCE|CE|BC|AD)?$/i, "")
        .replace(/,?\s*\d{2,4}\??\s*(?:BCE|CE|BC|AD)?$/i, "")
        .trim();
}

function createLibrarySearch() {
    const searchInput = createLineEdit({ value: searchState.query, placeholder: "Search title, author, or body" });
    searchInput.addEventListener("input", () => {
        searchState.query = searchInput.value;
    });
    searchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            searchCatalog();
        }
    });
    const limitInput = createLineEdit({ value: searchState.limit, placeholder: "50" });
    limitInput.addEventListener("input", () => {
        searchState.limit = limitInput.value;
    });
    const searchButton = createTextButton({ text: "Search", color: "#63d9a4" });
    searchButton.addEventListener("click", () => searchCatalog());
    const clearButton = createTextButton({ text: "Clear", color: "#78b0ff" });
    clearButton.addEventListener("click", () => {
        searchState = { query: "", limit: "50", results: [], searched: false };
        history.replaceState({}, "", "/ui/search");
        render();
    });
    return layout.stack([
        layout.searchRow([searchInput, limitInput, searchButton, clearButton]),
        searchState.searched
            ? createSearchResults()
            : layout.mutedText("Enter text to search the library.")
    ]);
}

function createSearchResults() {
    const results = searchState.results ?? [];
    if (!results.length) {
        return layout.mutedText("No matching books.");
    }
    return layout.itemList(results.map((book) => {
        const readButton = createTextButton({ text: "Read", color: "#63d9a4" });
        readButton.addEventListener("click", () => navigate(`/ui/books/${encodeURIComponent(book.route_id)}`));
        return layout.item([
            layout.titleRow(book.title ?? "", book.route_id ?? ""),
            layout.normalText(metadataLine(book)),
            layout.mutedText(cleanText(book.snippet ?? "")),
            layout.controlRow([readButton])
        ]);
    }));
}

function createLibraryRead() {
    if (!selectedBook) {
        return layout.mutedText("Book not found.");
    }
    return layout.stack([
        layout.mutedText(metadataLine(selectedBook)),
        layout.preformatted(readableBodyText(selectedBook) || "No body text stored for this book.")
    ], { fill: true });
}

function createLibraryImport() {
    return layout.stack([
        layout.sectionTitle("Manual import"),
        createBookForm({
            draft: importDraft,
            actionText: "Import",
            actionColor: "#f6c177",
            onSubmit: importBook,
            emptyText: "Import creates a new book in the selected catalog."
        }),
        layout.sectionTitle("Kiwix import"),
        createKiwixImport()
    ]);
}

function createKiwixImport() {
    const serverUrl = boundLine(kiwixDraft, "url", "Kiwix server URL");
    const zim = boundLine(kiwixDraft, "zim", "ZIM name");
    const query = boundLine(kiwixDraft, "query", "Search title, author, or text");
    const author = boundLine(kiwixDraft, "author", "Catalog author filter");
    const viewerUrl = boundLine(kiwixDraft, "viewerUrl", "Kiwix viewer URL");
    const batchUrls = boundArea(kiwixDraft, "batchUrls", "One Kiwix viewer URL per line", 8);
    const reloadButton = createTextButton({ text: "Reload", color: "#78b0ff" });
    reloadButton.addEventListener("click", loadKiwixInventory);
    const searchButton = createTextButton({ text: "Search", color: "#63d9a4" });
    searchButton.addEventListener("click", searchKiwix);
    const catalogButton = createTextButton({ text: "Catalog", color: "#f6c177" });
    catalogButton.addEventListener("click", loadKiwixCatalog);
    const viewerButton = createTextButton({ text: "Import URL", color: "#f6c177" });
    viewerButton.addEventListener("click", importViewerUrl);
    const batchButton = createTextButton({ text: "Start Batch", color: "#f6c177" });
    batchButton.addEventListener("click", batchImportViewerUrls);
    const clearLogButton = createTextButton({ text: "Clear Log", color: "#78b0ff" });
    clearLogButton.addEventListener("click", () => {
        importLog = [];
        render();
    });
    return layout.stack([
        layout.formGrid([serverUrl, zim, query, author]),
        layout.controlRow([reloadButton, searchButton, catalogButton, layout.mutedText(message, { tag: "span" })]),
        createKiwixInventoryList(),
        createKiwixResultList(),
        createKiwixCatalogList(),
        layout.actionRow([viewerUrl, viewerButton]),
        batchUrls,
        layout.controlRow([batchButton, clearLogButton]),
        createImportLog()
    ]);
}

function createLibraryEdit() {
    if (!selectedBook) {
        return layout.mutedText("Book not found.");
    }
    const saveButton = createTextButton({ text: "Save", color: "#63d9a4" });
    saveButton.addEventListener("click", saveBook);
    const cancelButton = createTextButton({ text: "Cancel", color: "#78b0ff" });
    cancelButton.addEventListener("click", () => navigate(`/ui/books/${encodeURIComponent(selectedBook.route_id)}`));
    const deleteButton = createTextButton({ text: "Delete", color: "#ff7070" });
    deleteButton.addEventListener("click", deleteBook);
    return layout.stack([
        createBookForm({
            draft: editDraft,
            actionText: "Save",
            actionColor: "#63d9a4",
            onSubmit: saveBook,
            showSubmit: false,
            fillBody: true
        }),
        layout.controlRow([saveButton, cancelButton, deleteButton])
    ], { formFill: true });
}

function createBookForm(spec) {
    const title = boundLine(spec.draft, "title", "Title");
    const author = boundLine(spec.draft, "author", "Author");
    const year = boundLine(spec.draft, "year", "Year");
    const language = boundLine(spec.draft, "language", "Language");
    const genre = boundLine(spec.draft, "genre", "Genre");
    const catalog = boundLine(spec.draft, "catalog", "Catalog");
    const notes = boundArea(spec.draft, "notes", "Notes", 5);
    const body = boundArea(spec.draft, "body", "Body text", 12, Boolean(spec.fillBody));
    const children = [
        layout.formGrid([title, author, year, language, genre, catalog]),
        notes,
        body
    ];
    if (spec.emptyText) {
        children.unshift(layout.mutedText(spec.emptyText));
    }
    if (spec.showSubmit !== false) {
        const button = createTextButton({ text: spec.actionText, color: spec.actionColor });
        button.addEventListener("click", spec.onSubmit);
        children.push(layout.controlRow([button, layout.mutedText(message, { tag: "span" })]));
    }
    return layout.form(children, { fill: spec.fillBody });
}

function createKiwixInventoryList() {
    if (!kiwixInventory.length) {
        return layout.mutedText("Reload inventory to list available ZIM files.");
    }
    return layout.itemList(kiwixInventory.slice(0, 24).map((item) => {
        const useButton = createTextButton({ text: "Use", color: "#78b0ff" });
        useButton.addEventListener("click", () => {
            kiwixDraft.zim = item.name ?? "";
            message = `Selected ${item.title || item.name}`;
            render();
        });
        return layout.item([
            layout.normalText(item.title || item.name || "", { tag: "b" }),
            layout.mutedText([item.name, item.author].filter(Boolean).join(" | ")),
            layout.controlRow([useButton])
        ]);
    }));
}

function createKiwixResultList() {
    if (!kiwixResults.length) {
        return layout.mutedText("Search Kiwix to import individual results.");
    }
    return layout.itemList(kiwixResults.map((item, index) => {
        const importButton = createTextButton({ text: "Import", color: "#f6c177" });
        importButton.addEventListener("click", () => importKiwixResult(item));
        return layout.item([
            layout.normalText(cleanText(item.label || item.value || ""), { tag: "b" }),
            layout.mutedText(cleanText(item.snippet || item.url || "")),
            layout.controlRow([importButton, layout.metaText(`result ${index + 1}`)])
        ]);
    }));
}

function createKiwixCatalogList() {
    if (!kiwixCatalog) {
        return layout.mutedText("Catalog loads Gutenberg-style ZIM indexes when available.");
    }
    const addAllButton = createTextButton({ text: "Add All To Batch", color: "#78b0ff" });
    addAllButton.addEventListener("click", addCatalogToBatch);
    const groups = (kiwixCatalog.authors ?? []).slice(0, 12);
    return layout.stack([
        layout.controlRow([
            layout.mutedText(`${kiwixCatalog.total ?? 0} catalog books`, { tag: "span" }),
            addAllButton
        ]),
        ...groups.map((group) => layout.item([
            layout.normalText(group.author || "Unknown author", { tag: "b" }),
            layout.itemList((group.books ?? []).slice(0, 8).map((book) => {
                const importButton = createTextButton({ text: "Import", color: "#f6c177" });
                importButton.addEventListener("click", () => importKiwixResult({ label: book.title, value: book.title, url: book.article_path }));
                return layout.splitActionRow(book.title || "", importButton);
            }))
        ]))
    ]);
}

function createImportLog() {
    if (!importLog.length) {
        return layout.mutedText("Import log is empty.");
    }
    return layout.preformatted(importLog.join("\n"), { log: true });
}

function boundLine(draft, key, placeholder) {
    const input = createLineEdit({ value: draft[key] ?? "", placeholder });
    input.addEventListener("input", () => {
        draft[key] = input.value;
    });
    return input;
}

function boundArea(draft, key, placeholder, rows, fill = false) {
    const input = createTextArea({ value: draft[key] ?? "", placeholder, rows, fill });
    input.addEventListener("input", () => {
        draft[key] = input.value;
    });
    return input;
}

async function searchCatalog(shouldRender = true) {
    if (!searchState.query.trim()) {
        searchState.results = [];
        searchState.searched = false;
        if (shouldRender) {
            render();
        }
        return;
    }
    history.replaceState({}, "", `/ui/search?q=${encodeURIComponent(searchState.query)}`);
    const data = await fetchJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: searchState.query, limit: Number(searchState.limit || 50) })
    });
    searchState.results = data.results ?? [];
    searchState.searched = true;
    if (shouldRender) {
        render();
    }
}

async function importBook() {
    if (!importDraft.title.trim()) {
        setMessage("Title is required.");
        return;
    }
    const book = await fetchJson("/api/books", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(importDraft)
    });
    importDraft = emptyBookDraft();
    message = "Book imported.";
    await navigate(`/ui/books/${encodeURIComponent(book.route_id)}`);
}

async function saveBook() {
    if (!selectedBook) {
        setMessage("Choose a book first.");
        return;
    }
    if (!editDraft.title.trim()) {
        setMessage("Title is required.");
        return;
    }
    selectedBook = await fetchJson(`/api/books/${encodeURIComponent(selectedBook.route_id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editDraft)
    });
    editDraft = draftFromBook(selectedBook);
    message = "Book saved.";
    await navigate(`/ui/books/${encodeURIComponent(selectedBook.route_id)}`);
}

async function deleteBook() {
    if (!selectedBook) {
        return;
    }
    await fetchJson(`/api/books/${encodeURIComponent(selectedBook.route_id)}`, { method: "DELETE" });
    message = "Book deleted.";
    await navigate("/ui");
}

async function loadKiwixInventory() {
    if (!kiwixDraft.url.trim()) {
        setMessage("Enter a Kiwix server URL first.");
        return;
    }
    try {
        const data = await fetchJson(`/api/import/kiwix/inventory?kiwix_url=${encodeURIComponent(kiwixDraft.url)}`);
        kiwixInventory = data.books ?? [];
        setMessage(`Loaded ${kiwixInventory.length} ZIM files.`);
    } catch (error) {
        setMessage(error.message);
    }
}

async function searchKiwix() {
    if (!kiwixDraft.url.trim() || !kiwixDraft.zim.trim() || !kiwixDraft.query.trim()) {
        setMessage("Enter Kiwix URL, ZIM name, and search text.");
        return;
    }
    try {
        kiwixResults = await fetchJson(
            `/api/import/kiwix/search?kiwix_url=${encodeURIComponent(kiwixDraft.url)}&zim=${encodeURIComponent(kiwixDraft.zim)}&q=${encodeURIComponent(kiwixDraft.query)}&count=100`
        );
        setMessage(`Found ${kiwixResults.length} Kiwix results.`);
    } catch (error) {
        setMessage(error.message);
    }
}

async function loadKiwixCatalog() {
    if (!kiwixDraft.url.trim() || !kiwixDraft.zim.trim()) {
        setMessage("Enter Kiwix URL and ZIM name.");
        return;
    }
    try {
        const authorQuery = kiwixDraft.author ? `&author=${encodeURIComponent(kiwixDraft.author)}` : "";
        kiwixCatalog = await fetchJson(
            `/api/import/kiwix/catalog?kiwix_url=${encodeURIComponent(kiwixDraft.url)}&zim=${encodeURIComponent(kiwixDraft.zim)}${authorQuery}`
        );
        setMessage(`Loaded ${kiwixCatalog.total ?? 0} catalog books.`);
    } catch (error) {
        setMessage(error.message);
    }
}

async function importKiwixResult(item) {
    const title = cleanText(item.value || item.label || "");
    if (!title || !kiwixDraft.url.trim() || !kiwixDraft.zim.trim()) {
        setMessage("Kiwix URL, ZIM name, and title are required.");
        return;
    }
    try {
        logImport(`-> ${title}`);
        const book = await fetchJson("/api/import/kiwix", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                kiwix_url: kiwixDraft.url,
                zim_name: kiwixDraft.zim,
                title,
                article_url: item.url || "",
                language: kiwixDraft.language,
                catalog: kiwixDraft.catalog
            })
        });
        logImport(`ok ${book.route_id} ${book.title}`);
        await navigate(`/ui/books/${encodeURIComponent(book.route_id)}`);
    } catch (error) {
        logImport(`error ${title}: ${error.message}`);
        setMessage(error.message);
    }
}

async function importViewerUrl() {
    if (!kiwixDraft.viewerUrl.trim()) {
        setMessage("Paste a viewer URL first.");
        return;
    }
    try {
        logImport(`-> ${kiwixDraft.viewerUrl}`);
        const book = await fetchJson("/api/import/kiwix/viewer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                viewer_url: kiwixDraft.viewerUrl,
                kiwix_url: kiwixDraft.url,
                language: kiwixDraft.language,
                catalog: kiwixDraft.catalog
            })
        });
        logImport(`ok ${book.route_id} ${book.title}`);
        kiwixDraft.viewerUrl = "";
        await navigate(`/ui/books/${encodeURIComponent(book.route_id)}`);
    } catch (error) {
        logImport(`error ${error.message}`);
        setMessage(error.message);
    }
}

async function batchImportViewerUrls() {
    const urls = kiwixDraft.batchUrls.split("\n").map((line) => line.trim()).filter((line) => line && !line.startsWith("#"));
    if (!urls.length) {
        setMessage("Paste at least one viewer URL.");
        return;
    }
    try {
        logImport(`-> batch ${urls.length} URL(s)`);
        const data = await fetchJson("/api/import/kiwix/viewer/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ urls, kiwix_url: kiwixDraft.url, language: kiwixDraft.language, catalog: kiwixDraft.catalog })
        });
        for (const item of data.results ?? []) {
            logImport(`${item.status} ${item.id || ""} ${item.title || item.url}${item.detail ? ` - ${item.detail}` : ""}`);
        }
        const summary = data.summary ?? {};
        setMessage(`Batch done: ${summary.ok ?? 0} imported, ${summary.exists ?? 0} existing, ${summary.error ?? 0} errors.`);
    } catch (error) {
        logImport(`error ${error.message}`);
        setMessage(error.message);
    }
}

function addCatalogToBatch() {
    const urls = [];
    for (const group of kiwixCatalog?.authors ?? []) {
        for (const book of group.books ?? []) {
            if (book.viewer_url) {
                urls.push(book.viewer_url);
            }
        }
    }
    if (!urls.length) {
        setMessage("No catalog URLs to add.");
        return;
    }
    kiwixDraft.batchUrls = [kiwixDraft.batchUrls.trim(), urls.join("\n")].filter(Boolean).join("\n");
    setMessage(`Added ${urls.length} URLs to batch.`);
}

function parseRoute() {
    const path = location.pathname.replace(/\/+$/, "") || "/ui";
    if (path === "/" || path === "/ui") {
        return { page: "catalog", bookId: "" };
    }
    if (path === "/ui/search") {
        return { page: "search", bookId: "" };
    }
    if (path === "/ui/import") {
        return { page: "import", bookId: "" };
    }
    const editMatch = path.match(/^\/ui\/books\/(.+)\/edit$/);
    if (editMatch) {
        return { page: "edit", bookId: decodeURIComponent(editMatch[1]) };
    }
    const readMatch = path.match(/^\/ui\/books\/(.+)$/);
    if (readMatch) {
        return { page: "read", bookId: decodeURIComponent(readMatch[1]) };
    }
    return { page: "catalog", bookId: "" };
}

function logImport(line) {
    importLog = [...importLog, line].slice(-200);
    render();
}

function setMessage(text) {
    message = text;
    render();
}

function metadataLine(book) {
    return [book.author, book.year, book.language, book.genre, book.word_count ? `${book.word_count} words` : ""]
        .filter(Boolean)
        .join(" | ") || "No metadata";
}

function cleanText(value) {
    return stripHtml(value);
}

function readableBodyText(book) {
    const body = String(book?.body || "");
    if (!body) {
        return "";
    }

    if (book?.source === "kiwix" || looksSoftWrapped(body)) {
        return normalizeImportedBodyText(body);
    }

    return body;
}

function normalizeImportedBodyText(value) {
    const text = String(value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const trimmed = text
        .split("\n")
        .map((line) => line.replace(/\s+$/g, ""))
        .join("\n")
        .trim();

    if (!trimmed) {
        return "";
    }

    return mergeLegacyWrappedBlocks(
        trimmed
        .split(/\n{2,}/)
        .map((block) => String(block || "").trim())
        .filter(Boolean)
    )
        .map((block) => normalizeImportedBlock(block))
        .filter(Boolean)
        .join("\n\n");
}

function mergeLegacyWrappedBlocks(blocks) {
    const merged = [];
    let paragraphLines = [];

    const flushParagraph = () => {
        if (paragraphLines.length) {
            merged.push(paragraphLines.join("\n"));
            paragraphLines = [];
        }
    };

    for (const block of blocks) {
        const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
        if (lines.length === 1 && isLegacyWrappedLine(lines[0], paragraphLines.length > 0)) {
            paragraphLines.push(lines[0]);
            continue;
        }

        flushParagraph();
        merged.push(block);
    }

    flushParagraph();
    return merged;
}

function isLegacyWrappedLine(line, continuingParagraph) {
    if (!line || /^(?:["'\u2018\u201c-]|[A-Z][A-Za-z]+:)/.test(line)) {
        return false;
    }

    const compact = line.replace(/\s+/g, " ").trim();
    const headingLike = compact.length <= 40 && compact === compact.toUpperCase();
    if (headingLike) {
        return false;
    }

    if (continuingParagraph) {
        return compact.length >= 25;
    }

    return compact.length >= 55;
}

function normalizeImportedBlock(block) {
    const lines = String(block || "")
        .split("\n")
        .map((line) => line.trim().replace(/\s+/g, " "))
        .filter(Boolean);

    if (!lines.length) {
        return "";
    }
    if (lines.length === 1) {
        return lines[0];
    }

    const lengths = [...lines].map((line) => line.length).sort((left, right) => left - right);
    const median = lengths[Math.floor(lengths.length / 2)];
    const average = lengths.reduce((sum, lineLength) => sum + lineLength, 0) / lengths.length;
    const dialogueCues = lines.filter((line) => /^(?:["'\u2018\u201c-]|[A-Z][A-Za-z]+:)/.test(line)).length;

    if (median >= 60 || (average >= 50 && lengths[lengths.length - 1] >= 72)) {
        if (dialogueCues * 2 < lines.length || average >= 65) {
            return lines.join(" ");
        }
    }

    return lines.join("\n");
}

function looksSoftWrapped(text) {
    const lines = String(text || "")
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);

    if (lines.length < 8) {
        return false;
    }

    const candidates = lines.filter((line) => line.length >= 55 && line.length <= 80);
    return candidates.length >= Math.max(6, Math.floor(lines.length * 0.5));
}

function emptyBookDraft() {
    return {
        title: "",
        author: "",
        year: "",
        language: "en",
        genre: "",
        catalog: "local",
        notes: "",
        body: ""
    };
}

function draftFromBook(book) {
    return {
        title: book.title ?? "",
        author: book.author ?? "",
        year: book.year ?? "",
        language: book.language ?? "",
        genre: book.genre ?? "",
        catalog: book.catalog ?? "local",
        notes: book.notes ?? "",
        body: book.body ?? ""
    };
}

function formatBytes(value) {
    const number = Number(value || 0);
    if (number < 1024) {
        return `${number} B`;
    }
    if (number < 1024 * 1024) {
        return `${(number / 1024).toFixed(1)} KB`;
    }
    return `${(number / (1024 * 1024)).toFixed(1)} MB`;
}

boot().catch((error) => {
    mount.replaceChildren(layout.shell([
        layout.normalText(error.message)
    ]));
});
