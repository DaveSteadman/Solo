import { el } from "./dom.js";

export function createTextLabel(spec) {
    return el("span", {
        className: "text-label font-normal",
        text: spec.text,
        style: {
            "--control-accent": spec.color
        }
    });
}
