import { el } from "./dom.js";
import { createIcon } from "./icon.js";

export function createIconTextButton(spec) {
    return el("button", {
        className: "text-button icon-text-button font-normal",
        style: {
            "--control-accent": spec.color
        },
        attrs: {
            type: "button"
        }
    }, [
        createIcon(spec.icon),
        el("span", { className: "font-normal", text: spec.text })
    ]);
}
