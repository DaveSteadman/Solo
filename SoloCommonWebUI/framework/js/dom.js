export function el(tagName, options = {}, children = []) {
    const element = document.createElement(tagName);

    if (options.className) {
        element.className = options.className;
    }

    if ("text" in options) {
        element.textContent = String(options.text);
    }

    if (options.attrs) {
        for (const [name, value] of Object.entries(options.attrs)) {
            if (value !== undefined && value !== null) {
                element.setAttribute(name, value);
            }
        }
    }

    if (options.style) {
        for (const [name, value] of Object.entries(options.style)) {
            if (value !== undefined && value !== null && value !== "") {
                element.style.setProperty(name, value);
            }
        }
    }

    appendChildren(element, children);
    return element;
}

export function appendChildren(parent, children) {
    for (const child of children) {
        if (child) {
            parent.append(child);
        }
    }
}
