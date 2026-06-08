import { el } from "./dom.js";

const keywords = new Set([
    "and", "as", "async", "await", "break", "case", "catch", "class", "const", "continue",
    "def", "default", "elif", "else", "export", "false", "finally", "for", "from", "function",
    "if", "import", "in", "let", "new", "none", "not", "null", "or", "pass", "return",
    "switch", "true", "try", "while", "yield"
]);

export function createCodeEditor(spec) {
    const lineList = el("div", { className: "code-editor-line-list font-normal" });
    const lineGutter = el("div", { className: "code-editor-lines" }, [lineList]);
    const highlight = el("pre", {
        className: "code-editor-highlight font-normal",
        attrs: {
            "aria-hidden": "true"
        }
    });
    const textarea = el("textarea", {
        className: "code-editor-input font-normal",
        attrs: {
            "aria-label": spec.label ?? "Code editor",
            "autocapitalize": "off",
            "autocomplete": "off",
            "autocorrect": "off",
            "spellcheck": "false",
            "wrap": "off"
        }
    });
    const editor = el("div", {
        className: "code-editor font-normal",
        attrs: {
            "aria-label": spec.label ?? "Code editor",
            "role": "region"
        }
    }, [
        lineGutter,
        el("div", { className: "code-editor-code" }, [highlight, textarea])
    ]);

    textarea.value = String(spec.code ?? "");
    sync();

    textarea.addEventListener("input", () => {
        sync();
        editor.dispatchEvent(new CustomEvent("solo:codeInput", {
            bubbles: true,
            detail: {
                value: textarea.value
            }
        }));
    });
    textarea.addEventListener("scroll", syncScroll);
    textarea.addEventListener("select", emitSelection);
    textarea.addEventListener("keyup", emitSelection);
    textarea.addEventListener("mouseup", emitSelection);
    textarea.addEventListener("keydown", (event) => {
        if (event.key !== "Tab") {
            return;
        }
        event.preventDefault();
        handleTab(textarea, event.shiftKey, spec.indent ?? "    ", sync);
    });

    function sync() {
        const lines = textarea.value.split(/\r?\n/);
        lineList.replaceChildren(...lines.map((_line, index) => (
            el("span", { className: "code-editor-line-number font-normal", text: index + 1 })
        )));
        highlight.replaceChildren(...lines.map((line, index) => {
            const children = highlightLine(line);
            if (index < lines.length - 1) {
                children.push(document.createTextNode("\n"));
            }
            return el("span", { className: "code-editor-line" }, children);
        }));
        syncScroll();
    }

    function syncScroll() {
        highlight.scrollTop = textarea.scrollTop;
        highlight.scrollLeft = textarea.scrollLeft;
        lineList.style.transform = `translateY(${-textarea.scrollTop}px)`;
    }

    function emitSelection() {
        editor.dispatchEvent(new CustomEvent("solo:codeSelection", {
            bubbles: true,
            detail: selectionDetail(textarea)
        }));
    }

    editor.getValue = () => textarea.value;
    editor.setValue = (value) => {
        textarea.value = String(value ?? "");
        sync();
    };
    editor.getSelectionDetail = () => selectionDetail(textarea);

    return editor;
}

function selectionDetail(textarea) {
    return {
        start: textarea.selectionStart,
        end: textarea.selectionEnd,
        text: textarea.value.slice(textarea.selectionStart, textarea.selectionEnd),
        startLine: lineForOffset(textarea.value, textarea.selectionStart),
        endLine: lineForOffset(textarea.value, textarea.selectionEnd)
    };
}

function lineForOffset(value, offset) {
    let line = 1;
    const limit = Math.max(0, Math.min(offset, value.length));
    for (let index = 0; index < limit; index += 1) {
        if (value[index] === "\n") {
            line += 1;
        }
    }
    return line;
}

function handleTab(textarea, outdent, indent, sync) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selectedText = textarea.value.slice(start, end);

    if (!outdent && start === end) {
        replaceWithUndo(textarea, start, end, indent);
        sync();
        return;
    }

    if (!outdent && !selectedText.includes("\n")) {
        replaceWithUndo(textarea, start, end, indent);
        sync();
        return;
    }

    const bounds = selectedLineBounds(textarea.value, start, end);
    const block = textarea.value.slice(bounds.start, bounds.end);
    const result = outdent ? outdentBlock(block, indent) : indentBlock(block, indent);

    replaceWithUndo(textarea, bounds.start, bounds.end, result.text);
    textarea.setSelectionRange(
        Math.max(bounds.start, start + result.startDelta),
        Math.max(bounds.start, end + result.endDelta)
    );
    sync();
}

function selectedLineBounds(value, start, end) {
    const lineStart = value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const adjustedEnd = end > start && value[end - 1] === "\n" ? end - 1 : end;
    const nextBreak = value.indexOf("\n", adjustedEnd);
    return {
        start: lineStart,
        end: nextBreak === -1 ? value.length : nextBreak
    };
}

function indentBlock(block, indent) {
    const lines = block.split("\n");
    return {
        text: lines.map((line) => indent + line).join("\n"),
        startDelta: indent.length,
        endDelta: indent.length * lines.length
    };
}

function outdentBlock(block, indent) {
    const lines = block.split("\n");
    let startDelta = 0;
    let endDelta = 0;
    let offset = 0;
    const outdented = lines.map((line) => {
        const removed = removableIndentLength(line, indent);
        if (offset === 0) {
            startDelta = -removed;
        }
        endDelta -= removed;
        offset += line.length + 1;
        return line.slice(removed);
    });
    return {
        text: outdented.join("\n"),
        startDelta,
        endDelta
    };
}

function removableIndentLength(line, indent) {
    if (line.startsWith("\t")) {
        return 1;
    }
    let count = 0;
    while (count < indent.length && line[count] === " ") {
        count += 1;
    }
    return count;
}

function replaceWithUndo(textarea, start, end, replacement) {
    textarea.focus();
    textarea.setSelectionRange(start, end);
    if (document.execCommand?.("insertText", false, replacement)) {
        return;
    }
    textarea.setRangeText(replacement, start, end, "end");
    textarea.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: replacement }));
}

function highlightLine(line) {
    const nodes = [];
    const pattern = /(#.*$|\/\/.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b)/g;
    let cursor = 0;
    let match;

    while ((match = pattern.exec(line)) !== null) {
        if (match.index > cursor) {
            nodes.push(...renderPlainText(line.slice(cursor, match.index)));
        }

        const value = match[0];
        nodes.push(el("span", {
            className: classForToken(value),
            text: value
        }));
        cursor = match.index + value.length;
    }

    if (cursor < line.length) {
        nodes.push(...renderPlainText(line.slice(cursor)));
    }

    return nodes;
}

function renderPlainText(text) {
    const nodes = [];
    let buffer = "";

    for (const char of text) {
        if (char !== " " && char !== "\t") {
            buffer += char;
            continue;
        }
        if (buffer) {
            nodes.push(document.createTextNode(buffer));
            buffer = "";
        }
        nodes.push(el("span", {
            className: char === "\t" ? "code-token-tab" : "code-token-space",
            text: char === "\t" ? "\u2192\t" : "\u00b7"
        }));
    }

    if (buffer) {
        nodes.push(document.createTextNode(buffer));
    }

    return nodes;
}

function classForToken(value) {
    if (value.startsWith("#") || value.startsWith("//")) {
        return "code-token-comment";
    }
    if (value.startsWith("\"") || value.startsWith("'")) {
        return "code-token-string";
    }
    if (/^\d/.test(value)) {
        return "code-token-number";
    }
    if (keywords.has(value.toLowerCase())) {
        return "code-token-keyword";
    }
    return "";
}
