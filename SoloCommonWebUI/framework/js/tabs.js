import { el } from "./dom.js";
import { createTextButton } from "./text-button.js";

export function createTabRow(spec) {
    return el("div", { className: "tab-row" }, (spec.items ?? []).map((item) => createTabButton({
        ...item,
        active: item.value === spec.activeValue,
        onSelect: () => spec.onSelect?.(item.value)
    })));
}

export function createTabButton(spec) {
    const button = createTextButton({
        text: spec.label ?? spec.text ?? "",
        color: spec.color ?? "#78b0ff"
    });
    button.classList.add("tab-button");
    if (spec.active) {
        button.classList.add("is-active");
    }
    button.addEventListener("click", () => spec.onSelect?.());
    return button;
}
