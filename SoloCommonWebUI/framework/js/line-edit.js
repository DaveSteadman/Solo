import { el } from "./dom.js";

export function createLineEdit(spec) {
    return el("input", {
        className: "line-edit font-normal",
        attrs: {
            type: "text",
            value: spec.value ?? "",
            placeholder: spec.placeholder ?? ""
        }
    });
}
