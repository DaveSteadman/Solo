import { el } from "./dom.js";
import { createIconButton } from "./icon-button.js";
import { createIconTextButton } from "./icon-text-button.js";
import { createTextButton } from "./text-button.js";
import { round_to_dp } from "./text-utils.js";

const actionFactories = {
    iconButton: createIconButton,
    iconTextButton: createIconTextButton,
    textButton: createTextButton
};
const sliderPersistenceSavers = new Map();
let sliderPersistenceBound = false;

export function createSliderPanel(spec, createControl = () => null) {
    const storageKey = sliderPanelStorageKey(spec);
    const requestedPanes = spec.panes?.length ? spec.panes : [
        { label: "Left panel" },
        { label: "Center panel" },
        { label: "Right panel" }
    ];
    const paneCount = requestedPanes.length === 2 ? 2 : 3;
    const panes = paneCount === 2 ? requestedPanes.slice(0, 2) : requestedPanes.slice(0, 3);
    const panel = el("section", {
        className: `slider-panel slider-panel--${paneCount}`,
        attrs: {
            "aria-label": spec.label ?? "Resizable panel layout"
        }
    });
    const saved = readSavedState(storageKey);
    const state = {
        left: Number(saved?.left ?? spec.left ?? (paneCount === 2 ? 24 : 33.333)),
        right: Number(saved?.right ?? spec.right ?? 66.666),
        active: null
    };
    const dividers = paneCount === 2
        ? [createDivider("left", "Resize panes")]
        : [
            createDivider("left", "Resize left panel"),
            createDivider("right", "Resize right panel")
        ];

    const children = paneCount === 2
        ? [
            createPane(panes[0], createControl),
            dividers[0],
            createPane(panes[1], createControl)
        ]
        : [
            createPane(panes[0], createControl),
            dividers[0],
            createPane(panes[1], createControl),
            dividers[1],
            createPane(panes[2], createControl)
        ];

    panel.append(el("div", { className: `slider-panel-panes slider-panel-panes--${paneCount}` }, children));

    function minPercent() {
        const width = panel.getBoundingClientRect().width;
        if (!Number.isFinite(width) || width <= 0) {
            return 0;
        }
        return Math.min(28, ((spec.minPanePixels ?? 160) / width) * 100);
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function applyState() {
        panel.style.setProperty("--slider-panel-left", state.left.toFixed(3));
        if (paneCount === 3) {
            panel.style.setProperty("--slider-panel-right", state.right.toFixed(3));
        }
    }

    function saveState() {
        if (!storageKey) {
            return;
        }
        try {
            const payload = paneCount === 2
                ? { left: round_to_dp(state.left) }
                : { left: round_to_dp(state.left), right: round_to_dp(state.right) };
            window.localStorage.setItem(storageKey, JSON.stringify(payload));
        } catch {
            return;
        }
    }

    function setDividerFromClientX(clientX) {
        const rect = panel.getBoundingClientRect();
        if (!Number.isFinite(rect.width) || rect.width <= 0) {
            return;
        }
        const percent = ((clientX - rect.left) / rect.width) * 100;
        const min = minPercent();

        if (paneCount === 2 || state.active === "left") {
            const max = paneCount === 2 ? 100 - min : state.right - min;
            state.left = clamp(percent, min, max);
        }

        if (paneCount === 3 && state.active === "right") {
            state.right = clamp(percent, state.left + min, 100 - min);
        }

        applyState();
        saveState();
    }

    for (const divider of dividers) {
        divider.addEventListener("pointerdown", (event) => {
            state.active = divider.dataset.divider;
            divider.setPointerCapture(event.pointerId);
            divider.classList.add("is-dragging");
            panel.classList.add("is-dragging");
            setDividerFromClientX(event.clientX);
        });

        divider.addEventListener("pointermove", (event) => {
            if (state.active) {
                setDividerFromClientX(event.clientX);
            }
        });

        divider.addEventListener("pointerup", (event) => {
            divider.releasePointerCapture(event.pointerId);
            divider.classList.remove("is-dragging");
            panel.classList.remove("is-dragging");
            state.active = null;
            saveState();
        });

        divider.addEventListener("pointercancel", () => {
            divider.classList.remove("is-dragging");
            panel.classList.remove("is-dragging");
            state.active = null;
            saveState();
        });
    }

    applyState();
    addLayoutPersistenceHandlers(storageKey, saveState);
    return panel;
}

function createPane(spec = {}, createControl) {
    const bodyClass = ["slider-panel-body"];
    if (spec.flush) {
        bodyClass.push("slider-panel-body--flush");
    }
    if (spec.scroll) {
        bodyClass.push("slider-panel-body--scroll");
    }

    return el("div", {
        className: "slider-panel-pane",
        attrs: {
            "aria-label": spec.label ?? "Panel"
        }
    }, [
        el("div", { className: "slider-panel-title-row" }, [
            el("h2", { className: "font-heading-3", text: spec.title ?? spec.label ?? "Panel" }),
            el("div", { className: "slider-panel-title-actions" }, (spec.actions ?? []).map(createAction))
        ]),
        el("div", { className: bodyClass.join(" ") }, (spec.items ?? []).map(createControl))
    ]);
}

function createDivider(key, label) {
    return el("div", {
        className: "slider-panel-divider",
        attrs: {
            "aria-label": label,
            "aria-orientation": "vertical",
            "data-divider": key,
            "role": "separator"
        }
    });
}

function createAction(spec) {
    const factory = actionFactories[spec.type ?? "textButton"];
    if (!factory) {
        throw new Error(`Unknown slider panel action type: ${spec.type}`);
    }
    return factory(spec);
}

function readSavedState(storageKey) {
    if (!storageKey) {
        return null;
    }
    try {
        const parsed = JSON.parse(window.localStorage.getItem(storageKey) ?? "null");
        if (Number.isFinite(parsed?.left) && (parsed?.right === undefined || Number.isFinite(parsed?.right))) {
            return parsed;
        }
    } catch {
        return null;
    }
    return null;
}

function sliderPanelStorageKey(spec) {
    if (spec.storageKey) {
        return spec.storageKey;
    }
    const pageKey = typeof window === "undefined"
        ? "server"
        : `${window.location.host}${window.location.pathname}`;
    const signature = [
        spec.id,
        spec.label,
        ...(spec.panes ?? []).map((pane) => pane.id ?? pane.title ?? pane.label ?? "pane")
    ].filter((value) => value !== undefined && value !== null && value !== "").join(".");
    return `solo.layout.slider-panel.${sanitizeStorageKey(pageKey)}.${sanitizeStorageKey(signature || "main")}`;
}

function sanitizeStorageKey(value) {
    return String(value ?? "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

function addLayoutPersistenceHandlers(storageKey, save) {
    if (typeof window === "undefined" || !storageKey) {
        return;
    }
    sliderPersistenceSavers.set(storageKey, save);
    if (sliderPersistenceBound) {
        return;
    }
    sliderPersistenceBound = true;
    window.addEventListener("pagehide", () => {
        for (const saveLatest of sliderPersistenceSavers.values()) {
            saveLatest();
        }
    });
}
