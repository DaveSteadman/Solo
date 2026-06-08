const colors = {
    soloagent: "#63d9a4",
    solochat: "#78b0ff",
    solocode: "#f6c177",
    solodata: "#ff8fab",
    solodocs: "#b8a1ff",
    solohub: "#eef3fb",
    sololibrary: "#d4e157",
    solollm: "#7bdff2",

    feeds: "#ff9db6",
    graph: "#ff6f9c",
    library: "#d4e157",
    rag: "#ffb3c7",
    reference: "#ffc7d6",
    scrape: "#ff7fad"
};

export function getColor(label, fallback = "#78b0ff") {
    return colors[String(label ?? "").trim().toLowerCase()] ?? fallback;
}

export function getServiceColor(label, fallback = "#78b0ff") {
    const normalized = String(label ?? "").trim().toLowerCase();
    if (normalized === "hub") {
        return getColor("solohub", fallback);
    }
    return getColor(normalized.startsWith("solo") ? normalized : `solo${normalized}`, getColor(normalized, fallback));
}

export function colorEntries() {
    return { ...colors };
}
