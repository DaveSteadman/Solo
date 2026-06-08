import { el } from "./dom.js";
import { createIcon } from "./icon.js";

export function createIconPanelGroup(items, spec = {}) {
    const classNames = ["icon-panel-group"];
    if (spec.className) {
        classNames.push(spec.className);
    }

    return el(spec.tagName ?? "section", {
        className: classNames.join(" "),
        style: spec.minWidth ? {
            "--icon-panel-min-width": spec.minWidth
        } : undefined,
        attrs: spec.attrs
    }, items);
}

export function createIconPanel(spec, createControl) {
    const color = requiredValue(spec.color, "color");
    const icon = requiredValue(spec.icon, "icon");
    const children = [];

    children.push(el("div", { className: "icon-panel-banner" }, [
        el("span", { className: "icon-panel-icon" }, [createIcon(icon)]),
        el("span", { className: "icon-panel-title-block" }, [
            spec.overline ? el("span", { className: "icon-panel-overline font-footnote", text: spec.overline }) : null,
            el("span", { className: "icon-panel-title font-normal", text: spec.title ?? "" })
        ])
    ]));

    if (spec.description) {
        children.push(el("p", { className: "icon-panel-description font-normal", text: spec.description }));
    }

    if (spec.meta?.length) {
        children.push(el("div", { className: "icon-panel-meta" }, spec.meta.map((item) => {
            return el("div", { className: "icon-panel-meta-row font-normal" }, [
                el("span", { className: "icon-panel-meta-key font-normal", text: item.label ?? "" }),
                el("span", { className: "icon-panel-meta-value font-normal", text: item.value ?? "" })
            ]);
        })));
    }

    if (spec.items?.length) {
        children.push(el("div", { className: "icon-panel-body" }, spec.items.map(createControl)));
    }

    if (spec.actions?.length) {
        children.push(el("div", { className: "icon-panel-actions" }, spec.actions.map(createControl)));
    }

    return el("article", {
        className: "icon-panel font-normal",
        style: {
            "--icon-panel-accent": color
        }
    }, children);
}

function requiredValue(value, name) {
    if (typeof value === "string" && value.trim() !== "") {
        return value;
    }

    if (name === "color") {
        throw new Error("iconPanel requires color");
    }

    throw new Error("iconPanel requires icon");
}
