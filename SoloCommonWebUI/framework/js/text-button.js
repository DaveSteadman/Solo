import { el } from "./dom.js";

export function createTextButton(spec) {
    return el("button", {
        className: "text-button font-normal",
        text: spec.text,
        style: {
            "--control-accent": spec.color
        },
        attrs: {
            type: "button"
        }
    });
}
