import { el } from "./dom.js";
import { createIconButton } from "./icon-button.js";

export function createServiceList(spec) {
    const services = spec.services ?? [];
    if (!services.length) {
        return el("p", { text: spec.emptyText ?? "No services are configured." });
    }

    return el("div", {
        className: "service-list",
        attrs: {
            "aria-label": spec.label ?? "Services"
        }
    }, services.map(createServiceRow));
}

function createServiceRow(service) {
    const start = createServiceAction(service, "start", "next", `Start ${service.label}`, !service.startable || service.running);
    const stop = createServiceAction(service, "stop", "export", `Stop ${service.label}`, !service.running);
    const restart = createServiceAction(service, "restart", "action", `Restart ${service.label}`, !service.startable);

    return el("article", { className: "service-list-row" }, [
        el("div", { className: "service-list-main" }, [
            el("h3", { className: "font-heading-3", text: service.label }),
            el("p", { className: "font-normal", text: service.description })
        ]),
        el("div", { className: "service-list-meta" }, [
            el("p", { className: "font-normal", text: service.slug }),
            el("code", { className: "font-normal", text: service.cwd })
        ]),
        el("div", { className: "service-list-meta" }, [
            el("span", { className: `font-normal service-list-state service-list-state--${service.state}`, text: service.stateLabel }),
            service.url ? el("a", { className: "font-normal", text: service.url, attrs: { href: service.url, target: "_blank", rel: "noreferrer" } }) : null
        ]),
        el("div", { className: "service-list-actions" }, [start, stop, restart])
    ]);
}

function createServiceAction(service, action, icon, label, disabled) {
    const button = createIconButton({ icon, label });
    button.dataset.action = action;
    button.dataset.service = service.slug;
    button.disabled = disabled;
    button.addEventListener("click", () => {
        button.dispatchEvent(new CustomEvent("solo:serviceAction", {
            bubbles: true,
            detail: {
                action,
                service
            }
        }));
    });
    return button;
}
