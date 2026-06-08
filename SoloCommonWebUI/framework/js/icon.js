import { el } from "./dom.js";
import { getIconUrl } from "./icon-registry.js";

export function createIcon(label) {
    return el("span", {
        className: "button-icon",
        style: {
            "--icon-url": `url("${getIconUrl(label)}")`
        },
        attrs: {
            "aria-hidden": "true"
        }
    });
}
