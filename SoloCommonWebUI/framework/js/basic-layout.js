import { el } from "./dom.js";
import { getServiceColor } from "./color-registry.js";
import { createIcon } from "./icon.js";
import { version } from "./version.js";

const panelGroupMemory = new Map();
const panelPersistenceSavers = new Map();
let panelPersistenceBound = false;

const DEFAULT_SERVICE_NAV = [
    { slug: "solohub", label: "Hub", icon: "data", port: 9700, path: "/ui" },
    { slug: "soloagent", label: "Agent", icon: "agent", port: 9710, path: "/ui" },
    { slug: "solochat", label: "Chat", icon: "chat", port: 9720, path: "/ui" },
    { slug: "solodata", label: "Data", icon: "data", port: 9740, path: "/ui" },
    { slug: "solodocs", label: "Docs", icon: "docs", port: 9750, path: "/ui" },
    { slug: "solocode", label: "Code", icon: "code", port: 9760, path: "/ui" }
];

export function createPage(spec, createControl) {
    const pageClassNames = ["page", "page--with-header", "page--with-project-bar"];

    const projectBarSpec = spec.projectBar ?? {};
    if (projectBarSpec.disabled) {
        pageClassNames.pop();
    }
    if (spec.shell?.viewport) {
        pageClassNames.push("page--viewport-shell");
    }
    if (spec.shell?.viewportLock) {
        pageClassNames.push("page--viewport-lock");
    }

    const page = el("div", { className: pageClassNames.join(" ") });
    if (!projectBarSpec.disabled) {
        page.append(createProjectBar(projectBarSpec));
    }
    page.append(createHeader(spec.header ?? {}, createControl));
    page.append(createShell(spec.content ?? [], createControl, spec.shell ?? {}));

    return page;
}

export function createProjectBar(spec = {}) {
    const services = spec.services ?? DEFAULT_SERVICE_NAV;
    const children = services.map(createProjectBarLink);
    if (spec.showVersion !== false) {
        children.push(createVersionLabel(spec.versionText ?? version));
    }
    return el("nav", {
        className: "page-project-bar",
        attrs: {
            "aria-label": spec.label ?? "Solo services"
        }
    }, [
        el("div", { className: "page-project-bar-inner page-top-bar-inner--full" }, children)
    ]);
}

function createVersionLabel(text) {
    return el("span", {
        className: "project-version-label font-normal",
        text,
        style: {
            "--control-accent": "#a5afbf"
        }
    });
}

function createProjectBarLink(service) {
    const href = service.href ?? serviceHref(service);
    const active = isCurrentService(href);
    return el("a", {
        className: `project-nav-button font-normal${active ? " is-active" : ""}`,
        style: {
            "--control-accent": service.color ?? getServiceColor(service.slug ?? service.label)
        },
        attrs: {
            href,
            "aria-current": active ? "page" : undefined
        }
    }, [
        createIcon(service.icon ?? service.slug ?? "preview"),
        el("span", { className: "font-normal", text: service.label ?? service.slug ?? "" })
    ]);
}

function serviceHref(service) {
    const protocol = window.location.protocol || "http:";
    const hostname = window.location.hostname || "127.0.0.1";
    const port = service.port ? `:${service.port}` : "";
    return `${protocol}//${hostname}${port}${service.path ?? "/ui"}`;
}

function isCurrentService(href) {
    try {
        const target = new URL(href, window.location.href);
        return target.host === window.location.host;
    } catch {
        return false;
    }
}

export function createHeader(spec, createControl) {
    return createServiceBanner(spec, createControl);
}

export function createServiceBanner(spec, createControl) {
    const service = serviceForBanner(spec);
    const color = spec.color ?? getServiceColor(service.slug ?? service.label);
    const icon = spec.icon ?? service.icon ?? "preview";
    const headerChildren = [
        el("span", { className: "service-banner-icon" }, [createIcon(icon)]),
        el("span", { className: "service-banner-title-block" }, [
            el("h1", { className: "page-header-title font-heading-3", text: spec.title ?? "" }),
            spec.subtitle !== undefined
                ? el("p", { className: "page-header-subtitle", text: spec.subtitle })
                : null
        ])
    ];

    const main = el("div", { className: "page-header-main service-banner-main" }, headerChildren);
    const actions = el("div", {
        className: "page-header-actions"
    }, (spec.actions ?? []).map(createControl));

    return el("header", {
        className: "page-header service-banner",
        style: {
            "--service-banner-accent": color
        }
    }, [
        el("div", { className: "page-header-inner" }, [
            main,
            actions
        ])
    ]);
}

function serviceForBanner(spec) {
    if (spec.service) {
        const requested = String(spec.service).trim().toLowerCase();
        const normalized = requested.startsWith("solo") ? requested : `solo${requested}`;
        return DEFAULT_SERVICE_NAV.find((service) => service.slug === normalized) ?? { slug: normalized, icon: spec.icon };
    }
    const title = String(spec.title ?? "").trim().toLowerCase();
    if (title.startsWith("solo")) {
        const slug = title.replace(/[^a-z0-9]/g, "");
        return DEFAULT_SERVICE_NAV.find((service) => service.slug === slug) ?? { slug, icon: spec.icon };
    }
    const port = Number.parseInt(window.location.port || "0", 10);
    return DEFAULT_SERVICE_NAV.find((service) => service.port === port) ?? { slug: title, icon: spec.icon };
}

export function createShell(items, createControl, spec = {}) {
    const classNames = ["page-shell", "page-stack"];
    if (spec.fullWidth) {
        classNames.push("page-shell--full");
    }
    if (spec.viewport) {
        classNames.push("page-shell--viewport");
    }
    if (spec.className) {
        classNames.push(spec.className);
    }

    return el("main", {
        className: classNames.join(" ")
    }, items.map((item) => createBlock(item, createControl)));
}

export function createSection(spec, createControl) {
    return el("section", { className: "page-section" }, (spec.items ?? []).map(createControl));
}

export function createPanels(spec, createControl) {
    const panelSpec = {
        ...spec,
        storageKey: panelsStorageKey(spec)
    };
    const classNames = ["page-panels"];
    if (panelSpec.columns === 2 && !panelSpec.resizable) {
        classNames.push("page-panels--two");
    }
    if (panelSpec.resizable && panelSpec.columns === 2) {
        classNames.push("page-panels--resizable-two");
    }
    if (panelSpec.resizable && panelSpec.columns === 3) {
        classNames.push("page-panels--resizable-three");
    }
    if (panelSpec.columns === 3 && !panelSpec.resizable) {
        classNames.push("page-panels--three");
    }
    if (panelSpec.columns === 4) {
        classNames.push("page-panels--four");
    }
    if (panelSpec.stretch) {
        classNames.push("page-panels--stretch");
    }
    const panel = el("section", {
        className: classNames.join(" ")
    });
    const items = panelSpec.items ?? [];
    if (panelSpec.resizable && panelSpec.columns === 2 && items.length >= 2) {
        panel.append(
            createPanelSlot(items[0], createControl),
            createPanelsDivider(),
            createPanelSlot(items[1], createControl)
        );
        wirePanelsDivider(panel, panelSpec);
        return panel;
    }
    if (panelSpec.resizable && panelSpec.columns === 3 && items.length >= 3) {
        panel.append(
            createPanelSlot(items[0], createControl),
            createPanelsDivider("first"),
            createPanelSlot(items[1], createControl),
            createPanelsDivider("second"),
            createPanelSlot(items[2], createControl)
        );
        wireThreePanelsDividers(panel, panelSpec);
        return panel;
    }
    panel.append(...items.map((item) => createPanelSlot(item, createControl)));
    return panel;
}

export function createPanelStack(spec, createControl) {
    const classNames = ["page-panel-stack"];
    if (spec.viewport) {
        classNames.push("page-panel-stack--viewport");
    }
    if (spec.className) {
        classNames.push(spec.className);
    }
    return el("section", {
        className: classNames.join(" ")
    }, (spec.items ?? []).map((item) => createPanelSlot(item, createControl)));
}

function createPanelSlot(spec, createControl) {
    if (spec.type === "panelStack") {
        return createPanelStack(spec, createControl);
    }
    if (spec.type === "panel" || spec.title !== undefined || spec.actions?.length || spec.items?.length) {
        return createPanel(spec, createControl);
    }
    return createControl(spec);
}

export function createPanel(spec, createControl) {
    const classNames = ["page-panel"];
    if (spec.viewport) {
        classNames.push("page-panel--viewport");
    }
    if (spec.className) {
        classNames.push(spec.className);
    }

    const titleChildren = [];

    if (spec.title !== undefined && spec.title !== "") {
        titleChildren.push(el("h2", { className: "font-heading-3", text: spec.title ?? "" }));
    }

    if (spec.actions?.length) {
        titleChildren.push(el("div", {
            className: "page-panel-title-actions"
        }, spec.actions.map(createControl)));
    }

    const children = [];
    if (titleChildren.length) {
        children.push(el("div", { className: "page-panel-title" }, titleChildren));
    }
    children.push(el("div", { className: "page-panel-body" }, (spec.items ?? []).map(createControl)));

    return el("article", {
        className: classNames.join(" "),
        attrs: {
            id: spec.id
        }
    }, children);
}

export function createControlRow(spec, createControl) {
    return el("div", { className: "control-demo-row" }, (spec.items ?? []).map(createControl));
}

export function createHeading(spec) {
    const level = Math.min(Math.max(spec.level ?? 1, 1), 3);
    return el(`h${level}`, { text: spec.text });
}

export function createParagraph(spec) {
    return el("p", { text: spec.text });
}

function createBlock(spec, createControl) {
    if (spec.type === "section") {
        return createSection(spec, createControl);
    }

    if (spec.type === "panels") {
        return createPanels(spec, createControl);
    }

    if (spec.type === "panelStack") {
        return createPanelStack(spec, createControl);
    }

    if (spec.type === "panel") {
        return createPanel(spec, createControl);
    }

    if (spec.type === "serviceBanner") {
        return createServiceBanner(spec, createControl);
    }

    return createControl(spec);
}

function createPanelsDivider(key = "") {
    return el("div", {
        className: "page-panels-divider",
        attrs: {
            "aria-label": "Resize panels",
            "aria-orientation": "vertical",
            "data-divider": key || undefined,
            "role": "separator"
        }
    });
}

function wirePanelsDivider(panel, spec) {
    const divider = panel.querySelector(".page-panels-divider");
    if (!divider) {
        return;
    }
    const saved = readSavedPanelsState(spec.storageKey);
    let left = Number(saved?.left ?? spec.left ?? 32);
    let dragging = false;

    function minPercent() {
        const width = panel.getBoundingClientRect().width;
        if (!Number.isFinite(width) || width <= 0) {
            return 0;
        }
        return Math.min(45, ((spec.minPanelPixels ?? 260) / width) * 100);
    }

    function clamp(value) {
        const min = minPercent();
        return Math.min(Math.max(value, min), 100 - min);
    }

    function apply() {
        panel.style.setProperty("--page-panels-left", `${clamp(left).toFixed(3)}%`);
    }

    function save() {
        if (spec.storageKey) {
            panelGroupMemory.set(spec.storageKey, { left: clamp(left) });
        }
        if (!spec.storageKey) {
            return;
        }
        try {
            window.localStorage.setItem(spec.storageKey, JSON.stringify({ left: clamp(left) }));
        } catch {
            return;
        }
    }

    function setFromClientX(clientX) {
        const rect = panel.getBoundingClientRect();
        if (!Number.isFinite(rect.width) || rect.width <= 0) {
            return;
        }
        left = ((clientX - rect.left) / rect.width) * 100;
        apply();
        save();
    }

    divider.addEventListener("pointerdown", (event) => {
        dragging = true;
        divider.setPointerCapture(event.pointerId);
        divider.classList.add("is-dragging");
        panel.classList.add("is-dragging");
        emitPanelsResizeEvent("solo:panels-resize-start", spec);
        setFromClientX(event.clientX);
    });
    divider.addEventListener("pointermove", (event) => {
        if (dragging) {
            setFromClientX(event.clientX);
        }
    });
    divider.addEventListener("pointerup", (event) => {
        dragging = false;
        divider.releasePointerCapture(event.pointerId);
        divider.classList.remove("is-dragging");
        panel.classList.remove("is-dragging");
        save();
        emitPanelsResizeEvent("solo:panels-resize-end", spec);
    });
    divider.addEventListener("pointercancel", () => {
        dragging = false;
        divider.classList.remove("is-dragging");
        panel.classList.remove("is-dragging");
        save();
        emitPanelsResizeEvent("solo:panels-resize-end", spec);
    });
    apply();
    addLayoutPersistenceHandlers(spec.storageKey, save);
}

function wireThreePanelsDividers(panel, spec) {
    const firstDivider = panel.querySelector('.page-panels-divider[data-divider="first"]');
    const secondDivider = panel.querySelector('.page-panels-divider[data-divider="second"]');
    if (!firstDivider || !secondDivider) {
        return;
    }
    const saved = readSavedPanelsState(spec.storageKey);
    let first = Number(saved?.first ?? spec.first ?? spec.left ?? 24);
    let second = Number(saved?.second ?? spec.second ?? 68);
    let activeDivider = null;

    function minPercent() {
        const width = panel.getBoundingClientRect().width;
        if (!Number.isFinite(width) || width <= 0) {
            return 0;
        }
        return Math.min(28, ((spec.minPanelPixels ?? 220) / width) * 100);
    }

    function clampPositions() {
        const min = minPercent();
        first = Math.min(Math.max(first, min), 100 - (min * 2));
        second = Math.min(Math.max(second, first + min), 100 - min);
    }

    function apply() {
        clampPositions();
        panel.style.setProperty("--page-panels-first-size", `${first.toFixed(3)}fr`);
        panel.style.setProperty("--page-panels-middle-size", `${(second - first).toFixed(3)}fr`);
        panel.style.setProperty("--page-panels-last-size", `${(100 - second).toFixed(3)}fr`);
    }

    function save() {
        clampPositions();
        const value = { first, second };
        if (spec.storageKey) {
            panelGroupMemory.set(spec.storageKey, value);
        }
        if (!spec.storageKey) {
            return;
        }
        try {
            window.localStorage.setItem(spec.storageKey, JSON.stringify(value));
        } catch {
            return;
        }
    }

    function setFromClientX(clientX) {
        const rect = panel.getBoundingClientRect();
        if (!Number.isFinite(rect.width) || rect.width <= 0) {
            return;
        }
        const value = ((clientX - rect.left) / rect.width) * 100;
        if (activeDivider === firstDivider) {
            first = value;
        }
        if (activeDivider === secondDivider) {
            second = value;
        }
        apply();
        save();
    }

    function startDrag(divider, event) {
        activeDivider = divider;
        divider.setPointerCapture(event.pointerId);
        divider.classList.add("is-dragging");
        panel.classList.add("is-dragging");
        emitPanelsResizeEvent("solo:panels-resize-start", spec);
        setFromClientX(event.clientX);
    }

    function moveDrag(event) {
        if (activeDivider) {
            setFromClientX(event.clientX);
        }
    }

    function endDrag(divider, event) {
        if (!activeDivider) {
            return;
        }
        activeDivider = null;
        divider.releasePointerCapture?.(event.pointerId);
        firstDivider.classList.remove("is-dragging");
        secondDivider.classList.remove("is-dragging");
        panel.classList.remove("is-dragging");
        save();
        emitPanelsResizeEvent("solo:panels-resize-end", spec);
    }

    for (const divider of [firstDivider, secondDivider]) {
        divider.addEventListener("pointerdown", (event) => startDrag(divider, event));
        divider.addEventListener("pointermove", moveDrag);
        divider.addEventListener("pointerup", (event) => endDrag(divider, event));
        divider.addEventListener("pointercancel", (event) => endDrag(divider, event));
    }
    apply();
    addLayoutPersistenceHandlers(spec.storageKey, save);
}

function emitPanelsResizeEvent(name, spec) {
    window.dispatchEvent(new CustomEvent(name, {
        detail: {
            storageKey: spec.storageKey ?? ""
        }
    }));
}

function readSavedPanelsState(storageKey) {
    if (!storageKey) {
        return null;
    }
    if (panelGroupMemory.has(storageKey)) {
        return panelGroupMemory.get(storageKey);
    }
    try {
        const parsed = JSON.parse(window.localStorage.getItem(storageKey) ?? "null");
        if (Number.isFinite(parsed?.left) || (Number.isFinite(parsed?.first) && Number.isFinite(parsed?.second))) {
            panelGroupMemory.set(storageKey, parsed);
            return parsed;
        }
        return null;
    } catch {
        return null;
    }
}

function panelsStorageKey(spec) {
    if (spec.storageKey) {
        return spec.storageKey;
    }
    if (!spec.resizable) {
        return "";
    }
    return defaultLayoutStorageKey("panels", spec);
}

function defaultLayoutStorageKey(kind, spec) {
    const pageKey = typeof window === "undefined"
        ? "server"
        : `${window.location.host}${window.location.pathname}`;
    const signature = [
        spec.id,
        spec.title,
        spec.columns,
        ...(spec.items ?? []).map((item) => item.id ?? item.title ?? item.type ?? "panel")
    ].filter((value) => value !== undefined && value !== null && value !== "").join(".");
    return `solo.layout.${kind}.${sanitizeStorageKey(pageKey)}.${sanitizeStorageKey(signature || "main")}`;
}

function sanitizeStorageKey(value) {
    return String(value ?? "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

function addLayoutPersistenceHandlers(storageKey, save) {
    if (typeof window === "undefined" || !storageKey) {
        return;
    }
    panelPersistenceSavers.set(storageKey, save);
    if (panelPersistenceBound) {
        return;
    }
    panelPersistenceBound = true;
    window.addEventListener("pagehide", () => {
        for (const saveLatest of panelPersistenceSavers.values()) {
            saveLatest();
        }
    });
}
