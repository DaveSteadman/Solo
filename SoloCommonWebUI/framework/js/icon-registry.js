const icons = {
    action:    new URL("../../assets/icons/action.svg", import.meta.url).href,
    agent:     new URL("../../assets/icons/circuit-svgrepo-com.svg", import.meta.url).href,
    arrowDown: new URL("../../assets/icons/arrow-down-svgrepo-com.svg", import.meta.url).href,
    arrowUp:   new URL("../../assets/icons/arrow-up-svgrepo-com.svg", import.meta.url).href,
    chat:      new URL("../../assets/icons/chats-svgrepo-com.svg", import.meta.url).href,
    code:      new URL("../../assets/icons/code-svgrepo-com.svg", import.meta.url).href,
    data:      new URL("../../assets/icons/database-svgrepo-com.svg", import.meta.url).href,
    docs:      new URL("../../assets/icons/file-alt-svgrepo-com.svg", import.meta.url).href,
    export:    new URL("../../assets/icons/export.svg", import.meta.url).href,
    feeds:     new URL("../../assets/icons/rss-svgrepo-com.svg", import.meta.url).href,
    graph:     new URL("../../assets/icons/chart-network-svgrepo-com.svg", import.meta.url).href,
    library:   new URL("../../assets/icons/book-user-svgrepo-com.svg", import.meta.url).href,
    llm:       new URL("../../assets/icons/atom-svgrepo-com.svg", import.meta.url).href,
    next:      new URL("../../assets/icons/next.svg", import.meta.url).href,
    preview:   new URL("../../assets/icons/preview.svg", import.meta.url).href,
    rag:       new URL("../../assets/icons/shapes-svgrepo-com.svg", import.meta.url).href,
    reference: new URL("../../assets/icons/graduation-hat-alt-1-svgrepo-com.svg", import.meta.url).href,
    tag:       new URL("../../assets/icons/tag.svg", import.meta.url).href,
    scrape:    new URL("../../assets/icons/truck-svgrepo-com.svg", import.meta.url).href
};

export function getIconUrl(label) {
    const url = icons[label];

    if (!url) {
        throw new Error(`Unknown icon label: ${label}`);
    }

    return url;
}
