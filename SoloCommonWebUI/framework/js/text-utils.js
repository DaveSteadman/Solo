export function stripHtml(value) {
    const div = document.createElement("div");
    div.innerHTML = String(value ?? "");
    return div.textContent || div.innerText || "";
}

export function round_to_dp(value, decimalPlaces = 2) {
    const numeric = Number(value);
    const digits = Number(decimalPlaces);
    if (!Number.isFinite(numeric)) {
        return 0;
    }
    if (!Number.isInteger(digits) || digits < 0) {
        return numeric;
    }
    const factor = 10 ** digits;
    return Math.round(numeric * factor) / factor;
}

export function formatNumber(value, locale = "en-GB") {
    const numeric = Number(value || 0);
    return Number.isFinite(numeric) ? numeric.toLocaleString(locale) : "0";
}

export function formatDateTime(value, locale = "en-GB") {
    if (!value) {
        return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return date.toLocaleString(locale, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
    });
}
