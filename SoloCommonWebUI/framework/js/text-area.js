import { el } from "./dom.js";

export function createTextArea(spec) {
    const classNames = ["text-area", "font-normal"];
    if (spec.className) {
        classNames.push(spec.className);
    }
    if (spec.fill) {
        classNames.push("text-area--fill");
    }

    const area = el("textarea", {
        className: classNames.join(" "),
        attrs: {
            placeholder: spec.placeholder ?? "",
            rows: spec.rows ?? 8
        }
    });
    area.value = spec.value ?? "";
    return area;
}
