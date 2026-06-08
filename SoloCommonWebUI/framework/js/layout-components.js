import { el } from "./dom.js";

export function stack(children, options = {}) {
    const classNames = ["layout-stack"];
    if (options.fill) {
        classNames.push("page-fill-stack");
    }
    if (options.formFill) {
        classNames.push("page-form-fill-stack");
    }
    return el("div", { className: classNames.join(" ") }, children);
}

export function grid(children) {
    return el("div", { className: "layout-grid" }, children);
}

export function metricGrid(entries) {
    return el("div", { className: "layout-metric-grid" }, entries.map(([label, value]) => metric(label, value)));
}

export function formGrid(children) {
    return el("div", { className: "layout-form-grid" }, children);
}

export function searchRow(children, options = {}) {
    const classNames = ["layout-search-row"];
    if (options.compact) {
        classNames.push("layout-search-row--compact");
    }
    if (options.singleAction) {
        classNames.push("layout-search-row--single-action");
    }
    return el("div", { className: classNames.join(" ") }, children);
}

export function actionRow(children) {
    return el("div", { className: "layout-action-row" }, children);
}

export function controlRow(children) {
    return el("div", { className: "control-demo-row" }, children);
}

export function filterRow(children) {
    return el("div", { className: "layout-filter-row" }, children);
}

export function splitActionRow(label, action) {
    return el("div", { className: "layout-split-action-row" }, [
        normalText(label, { tag: "span" }),
        action
    ]);
}

export function itemList(children) {
    return el("div", { className: "layout-item-list" }, children);
}

export function pathList(rows) {
    return stack(rows.map(([label, value]) => pathRow(label, value)));
}

export function pathRow(label, value) {
    return el("div", { className: "layout-path-row" }, [
        normalText(label, { tag: "b" }),
        el("code", { className: "font-normal", text: value || "-" })
    ]);
}

export function item(children) {
    return el("article", { className: "layout-item" }, children);
}

export function titleRow(title, meta) {
    const children = [el("b", { className: "font-normal", text: title ?? "" })];
    if (meta !== undefined) {
        children.push(metaText(meta, { tag: "span" }));
    }
    return el("div", { className: "layout-title-row" }, children);
}

export function normalText(text, options = {}) {
    return el(options.tag ?? "p", { className: "font-normal", text: text ?? "" });
}

export function mutedText(text, options = {}) {
    return el(options.tag ?? "p", { className: "layout-muted font-normal", text: text ?? "" });
}

export function metaText(text, options = {}) {
    return el(options.tag ?? "span", { className: "layout-meta font-normal", text: text ?? "" });
}

export function codeText(text) {
    return el("code", { className: "font-normal", text: text ?? "" });
}

export function errorText(text, options = {}) {
    return el(options.tag ?? "p", { className: "layout-error font-normal", text: text ?? "" });
}

export function headingValue(text) {
    return el("span", { className: "font-heading-3", text: text ?? "" });
}

export function preformatted(text, options = {}) {
    const classNames = ["layout-preformatted", "font-normal"];
    if (options.log) {
        classNames.push("layout-log");
    }
    if (options.nowrap) {
        classNames.push("layout-preformatted--nowrap");
    }
    if (options.fiveLines) {
        classNames.push("layout-preformatted--five-lines");
    }
    return el("pre", { className: classNames.join(" "), text: text ?? "" });
}

export function metric(label, value) {
    return el("div", { className: "layout-metric" }, [
        normalText(label, { tag: "b" }),
        headingValue(value)
    ]);
}

export function statusBadge(spec = {}) {
    const online = Boolean(spec.online);
    const text = spec.text ?? (online ? "ONLINE" : "OFFLINE");
    return el("span", { className: `layout-status-badge ${online ? "is-online" : "is-offline"}` }, [
        el("span", { className: "layout-status-dot" }),
        el("span", { text })
    ]);
}

export function compactKeyValue(label, value) {
    return el("div", { className: "layout-compact-kv" }, [
        el("span", { className: "layout-compact-kv-key", text: label ?? "" }),
        el("span", { className: "layout-compact-kv-value", text: value ?? "" })
    ]);
}

export function metadataStrip(entries) {
    return el("div", { className: "layout-metadata-strip" }, entries.map(([label, value]) =>
        el("span", { className: "layout-metadata-strip-item font-normal" }, [
            el("span", { className: "layout-metadata-strip-label", text: `${label}:` }),
            el("span", { className: "layout-metadata-strip-value", text: value ?? "-" })
        ])
    ));
}

export function table(headers, rows) {
    return el("table", { className: "layout-table" }, [
        el("thead", {}, [
            el("tr", {}, headers.map((header) => el("th", { text: header })))
        ]),
        el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell) => el("td", { text: cell })))))
    ]);
}

export function groupHeading(text) {
    return el("div", { className: "layout-group-heading font-normal", text: text ?? "" });
}

export function groupedResults(groups) {
    return stack(groups.map((group) => {
        const children = [groupHeading(group.label)];
        if (group.error) {
            children.push(errorText(group.error));
        } else if (group.items?.length) {
            children.push(...group.items.map(resultCard));
        } else {
            children.push(mutedText(group.emptyText ?? "No results."));
        }
        return el("section", { className: "layout-result-group" }, children);
    }));
}

export function resultCard(result) {
    return item([
        result.type ? el("div", { className: "layout-result-type font-normal", text: String(result.type).replaceAll("_", " ") }) : null,
        titleRow(result.title ?? "", result.source ?? ""),
        result.path ? el("code", { className: "font-normal", text: result.path }) : null,
        result.snippet ? normalText(result.snippet) : null
    ].filter(Boolean));
}

export function form(children, options = {}) {
    const classNames = ["layout-form"];
    if (options.fill) {
        classNames.push("layout-form--fill");
    }
    return el("div", { className: classNames.join(" ") }, children);
}

export function sectionTitle(text) {
    return el("div", { className: "layout-section-title font-normal", text });
}

export function shell(children) {
    return el("main", { className: "page-shell" }, children);
}
