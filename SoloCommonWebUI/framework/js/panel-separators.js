import { el } from "./dom.js";
import { round_to_dp } from "./text-utils.js";

const panelGroupMemory = new Map();
const panelPersistenceSavers = new Map();
let panelPersistenceBound = false;

export function createPanelsDivider(key = "") {
    return el("div", {
        className: "page-panels-divider",
        attrs: {
            "aria-label": "Resize panels",
            "aria-orientation": "vertical",
            "data-divider": key || undefined,
            "role": "separator"
        }
    });
}

export function wirePanelsDivider(panel, spec) {
    const divider = panel.querySelector(".page-panels-divider");
    if (!divider) {
        return;
    }
    const saved = readSavedPanelsState(spec.storageKey);
    let left = Number(saved?.left ?? spec.left ?? 32);
    let dragging = false;

    function minPercent() {
        const width = panel.getBoundingClientRect().width;
        if (!Number.isFinite(width) || width <= 0) {
            return 0;
        }
        return Math.min(45, ((spec.minPanelPixels ?? 260) / width) * 100);
    }

    function clamp(value) {
        const min = minPercent();
        return Math.min(Math.max(value, min), 100 - min);
    }

    function apply() {
        panel.style.setProperty("--page-panels-left", `${clamp(left).toFixed(3)}%`);
    }

    function save() {
        const value = { left: round_to_dp(clamp(left)) };
        if (spec.storageKey) {
            panelGroupMemory.set(spec.storageKey, value);
        }
        if (!spec.storageKey) {
            return;
        }
        try {
            window.localStorage.setItem(spec.storageKey, JSON.stringify(value));
        } catch {
            return;
        }
    }

    function setFromClientX(clientX) {
        const rect = panel.getBoundingClientRect();
        if (!Number.isFinite(rect.width) || rect.width <= 0) {
            return;
        }
        left = ((clientX - rect.left) / rect.width) * 100;
        apply();
        save();
    }

    divider.addEventListener("pointerdown", (event) => {
        dragging = true;
        divider.setPointerCapture(event.pointerId);
        divider.classList.add("is-dragging");
        panel.classList.add("is-dragging");
        emitPanelsResizeEvent("solo:panels-resize-start", spec);
        setFromClientX(event.clientX);
    });
    divider.addEventListener("pointermove", (event) => {
        if (dragging) {
            setFromClientX(event.clientX);
        }
    });
    divider.addEventListener("pointerup", (event) => {
        dragging = false;
        divider.releasePointerCapture(event.pointerId);
        divider.classList.remove("is-dragging");
        panel.classList.remove("is-dragging");
        save();
        emitPanelsResizeEvent("solo:panels-resize-end", spec);
    });
    divider.addEventListener("pointercancel", () => {
        dragging = false;
        divider.classList.remove("is-dragging");
        panel.classList.remove("is-dragging");
        save();
        emitPanelsResizeEvent("solo:panels-resize-end", spec);
    });
    apply();
    addLayoutPersistenceHandlers(spec.storageKey, save);
}

export function wireThreePanelsDividers(panel, spec) {
    const firstDivider = panel.querySelector('.page-panels-divider[data-divider="first"]');
    const secondDivider = panel.querySelector('.page-panels-divider[data-divider="second"]');
    if (!firstDivider || !secondDivider) {
        return;
    }
    const saved = readSavedPanelsState(spec.storageKey);
    let first = Number(saved?.first ?? spec.first ?? spec.left ?? 24);
    let second = Number(saved?.second ?? spec.second ?? 68);
    let activeDivider = null;

    function minPercent() {
        const width = panel.getBoundingClientRect().width;
        if (!Number.isFinite(width) || width <= 0) {
            return 0;
        }
        return Math.min(28, ((spec.minPanelPixels ?? 220) / width) * 100);
    }

    function clampPositions() {
        const min = minPercent();
        first = Math.min(Math.max(first, min), 100 - (min * 2));
        second = Math.min(Math.max(second, first + min), 100 - min);
    }

    function apply() {
        clampPositions();
        panel.style.setProperty("--page-panels-first-size", `${first.toFixed(3)}fr`);
        panel.style.setProperty("--page-panels-middle-size", `${(second - first).toFixed(3)}fr`);
        panel.style.setProperty("--page-panels-last-size", `${(100 - second).toFixed(3)}fr`);
    }

    function save() {
        clampPositions();
        const value = { first: round_to_dp(first), second: round_to_dp(second) };
        if (spec.storageKey) {
            panelGroupMemory.set(spec.storageKey, value);
        }
        if (!spec.storageKey) {
            return;
        }
        try {
            window.localStorage.setItem(spec.storageKey, JSON.stringify(value));
        } catch {
            return;
        }
    }

    function setFromClientX(clientX) {
        const rect = panel.getBoundingClientRect();
        if (!Number.isFinite(rect.width) || rect.width <= 0) {
            return;
        }
        const value = ((clientX - rect.left) / rect.width) * 100;
        if (activeDivider === firstDivider) {
            first = value;
        }
        if (activeDivider === secondDivider) {
            second = value;
        }
        apply();
        save();
    }

    function startDrag(divider, event) {
        activeDivider = divider;
        divider.setPointerCapture(event.pointerId);
        divider.classList.add("is-dragging");
        panel.classList.add("is-dragging");
        emitPanelsResizeEvent("solo:panels-resize-start", spec);
        setFromClientX(event.clientX);
    }

    function moveDrag(event) {
        if (activeDivider) {
            setFromClientX(event.clientX);
        }
    }

    function endDrag(divider, event) {
        if (!activeDivider) {
            return;
        }
        activeDivider = null;
        divider.releasePointerCapture?.(event.pointerId);
        firstDivider.classList.remove("is-dragging");
        secondDivider.classList.remove("is-dragging");
        panel.classList.remove("is-dragging");
        save();
        emitPanelsResizeEvent("solo:panels-resize-end", spec);
    }

    for (const divider of [firstDivider, secondDivider]) {
        divider.addEventListener("pointerdown", (event) => startDrag(divider, event));
        divider.addEventListener("pointermove", moveDrag);
        divider.addEventListener("pointerup", (event) => endDrag(divider, event));
        divider.addEventListener("pointercancel", (event) => endDrag(divider, event));
    }
    apply();
    addLayoutPersistenceHandlers(spec.storageKey, save);
}

export function panelsStorageKey(spec) {
    if (spec.storageKey) {
        return spec.storageKey;
    }
    if (!spec.resizable) {
        return "";
    }
    return defaultLayoutStorageKey("panels", spec);
}

function emitPanelsResizeEvent(name, spec) {
    window.dispatchEvent(new CustomEvent(name, {
        detail: {
            storageKey: spec.storageKey ?? ""
        }
    }));
}

function readSavedPanelsState(storageKey) {
    if (!storageKey) {
        return null;
    }
    if (panelGroupMemory.has(storageKey)) {
        return panelGroupMemory.get(storageKey);
    }
    try {
        const parsed = JSON.parse(window.localStorage.getItem(storageKey) ?? "null");
        if (Number.isFinite(parsed?.left) || (Number.isFinite(parsed?.first) && Number.isFinite(parsed?.second))) {
            panelGroupMemory.set(storageKey, parsed);
            return parsed;
        }
        return null;
    } catch {
        return null;
    }
}

function defaultLayoutStorageKey(kind, spec) {
    const pageKey = typeof window === "undefined"
        ? "server"
        : `${window.location.host}${window.location.pathname}`;
    const signature = [
        spec.id,
        spec.title,
        spec.columns,
        ...(spec.items ?? []).map((item) => item.id ?? item.title ?? item.type ?? "panel")
    ].filter((value) => value !== undefined && value !== null && value !== "").join(".");
    return `solo.layout.${kind}.${sanitizeStorageKey(pageKey)}.${sanitizeStorageKey(signature || "main")}`;
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
    panelPersistenceSavers.set(storageKey, save);
    if (panelPersistenceBound) {
        return;
    }
    panelPersistenceBound = true;
    window.addEventListener("pagehide", () => {
        for (const saveLatest of panelPersistenceSavers.values()) {
            saveLatest();
        }
    });
}