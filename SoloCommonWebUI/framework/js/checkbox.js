import { el } from "./dom.js";

export function createCheckbox(spec) {
    const input = el("input", {
        className: "checkbox-input",
        attrs: {
            type: "checkbox",
            value: spec.value ?? "",
            name: spec.name ?? undefined
        }
    });
    input.checked = Boolean(spec.checked);
    input.disabled = Boolean(spec.disabled);

    return el("label", {
        className: "checkbox font-normal",
        style: {
            "--control-accent": spec.color
        }
    }, [
        input,
        el("span", { className: "checkbox-box", attrs: { "aria-hidden": "true" } }),
        el("span", { className: "checkbox-text", text: spec.text ?? spec.label ?? "" })
    ]);
}
