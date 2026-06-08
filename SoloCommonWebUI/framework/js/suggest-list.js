import { el } from "./dom.js";

export function createSuggestList() {
    const list = el("div", { className: "suggest-list font-normal" });
    list.hidden = true;
    document.body.appendChild(list);
    return list;
}

export function renderSuggestList(list, spec) {
    const items = spec.items ?? [];
    const activeIndex = Number.isInteger(spec.activeIndex) ? spec.activeIndex : -1;
    const onSelect = typeof spec.onSelect === "function" ? spec.onSelect : () => {};
    list.replaceChildren(...items.map((item, index) => {
        const row = el("div", {
            className: `suggest-list-item${index === activeIndex ? " suggest-list-item--active" : ""}`
        }, [
            el("span", { className: "suggest-list-item-name font-normal", text: item.value ?? "" }),
            el("span", { className: "suggest-list-item-detail font-normal", text: item.detail ?? "" })
        ]);
        row.addEventListener("mousedown", (event) => {
            event.preventDefault();
            onSelect(index);
        });
        return row;
    }));

    const anchor = spec.anchor;
    if (anchor) {
        const rect = anchor.getBoundingClientRect();
        const longest = items.reduce((max, item) => Math.max(max, String(item.value ?? "").length + String(item.detail ?? "").length), 0);
        list.style.left = `${rect.left}px`;
        list.style.width = `${Math.min(Math.max(240, longest * 7 + 48), rect.width)}px`;
        list.style.bottom = `${window.innerHeight - rect.top}px`;
    }
    list.hidden = false;
}

export function hideSuggestList(list) {
    list.hidden = true;
}
