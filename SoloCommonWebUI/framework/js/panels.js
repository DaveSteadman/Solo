import { el } from "./dom.js";
import {
    createPanelsDivider,
    panelsStorageKey,
    wirePanelsDivider,
    wireThreePanelsDividers,
} from "./panel-separators.js";

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

function createPanelSlot(spec, createControl) {
    if (spec.type === "panelStack") {
        return createPanelStack(spec, createControl);
    }
    if (spec.type === "panel" || spec.title !== undefined || spec.actions?.length || spec.items?.length) {
        return createPanel(spec, createControl);
    }
    return createControl(spec);
}