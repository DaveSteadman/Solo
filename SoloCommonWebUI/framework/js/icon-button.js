import { el } from "./dom.js";
import { createIcon } from "./icon.js";

export function createIconButton(spec) {
    const label = spec.label ?? spec.text ?? spec.icon;

    return el("button", {
        className: "icon-button",
        style: {
            "--control-accent": spec.color
        },
        attrs: {
            "aria-label": label,
            type: "button"
        }
    }, [
        createIcon(spec.icon)
    ]);
}
