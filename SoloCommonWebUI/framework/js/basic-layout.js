import { el } from "./dom.js";
import { getServiceColor } from "./color-registry.js";
import { createIcon } from "./icon.js";
import { createPanel, createPanels, createPanelStack } from "./panels.js";
import { version } from "./version.js";

export { createPanel, createPanels, createPanelStack };

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

